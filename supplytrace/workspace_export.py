"""One SQLite snapshot -> file-backed ZIP. Rows/events are streamed from cursors."""
import csv
import hashlib
import io
import json
import os
import time
import uuid
import zipfile

from .csv_engine import packed
from .models import WorkflowError
from .storage import now


def safe_cell(value):
    text = str(value)
    return "'"+text if text and (text.lstrip().startswith(('=','+','-','@')) or text[0] in '\t\r\n') else text


def write_array(stream, iterable):
    stream.write(b'[')
    for n, value in enumerate(iterable):
        if n:
            stream.write(b',')
        stream.write(packed(value).encode())
    stream.write(b']')


def build_export(wb, ident):
    started = time.monotonic()
    with wb.store.connect() as db:
        db.execute('BEGIN')
        r = wb.item(db,ident)
        if r['state']!='ready':
            raise WorkflowError('analysis_not_ready',409)
        target = wb.artifacts/f"export-{ident}-{r['generation']}-{r['revision']}.zip"
        # The artifact name binds an immutable snapshot, never a stale mutable query.
        if target.is_file():
            receipt = target.with_suffix('.sha256')
            if not receipt.is_file():
                raise WorkflowError('export_hash_mismatch',422)
            with target.open('rb') as stream:
                actual = hashlib.file_digest(stream,'sha256').hexdigest()
            if actual!=receipt.read_text(encoding='ascii').strip():
                raise WorkflowError('export_hash_mismatch',422)
            return target
        wb.disk_guard(r['byte_size']*8)
        partial = wb.artifacts/(target.name+'.'+uuid.uuid4().hex+'.partial')
        parent = db.execute('SELECT traceparent FROM imports WHERE id=?',(ident,)).fetchone()[0]
        with wb.tracing.span('csv.export',parent):
            def bounded(cursor, transform=dict):
                for row in cursor:
                    if time.monotonic()-started>180 or wb.stop.is_set():
                        raise WorkflowError('export_timeout',503)
                    yield transform(row)
            with zipfile.ZipFile(partial,'x',zipfile.ZIP_DEFLATED,compresslevel=1) as archive:
                digest = hashlib.sha256()
                with archive.open('original.csv','w',force_zip64=True) as out, wb.open_original_blob(db,ident) as blob:
                    while chunk := blob.read(65536):
                        digest.update(chunk)
                        out.write(chunk)
                if digest.hexdigest()!=r['sha256']:
                    raise WorkflowError('source_hash_mismatch',422)
                manifest = {'schema':'supplytrace-handover-v2','exported_at':now(),'import':wb.public(r),
                    'pending_groups':r['group_count']-r['reviewed_count'],'source_rows_modified':False,
                    'business_decision_allowed':False,'reviewer_identity':'self_declared',
                    'evidence_membership':'group-members.jsonl; one record per member, ordered by group and source row',
                    'evidence_hash':'ordered-row-hashes-v1: sha256 of lowercase sha256(packed(row)) plus LF per member; packed uses UTF-8 compact JSON in row_no,line_start,line_end,cells,parse_error order',
                    'csv_formula_escaping':'Derived cells starting with = + - @ or control characters have apostrophe prefix. Original CSV and evidence JSON retain exact values.'}
                if r['origin']=='uci_example':
                    from .config import ROOT
                    sample = json.loads((ROOT/'data/real-samples/uci-order-identities.json').read_text(encoding='utf-8'))
                    manifest['provenance'] = {k:v for k,v in sample.items() if k!='cases'}
                    manifest['provenance']['excerpt_note'] = 'UCI_source_CSV_line identifies the original dataset position; physical lines refer to this excerpt.'
                archive.writestr('manifest.json',packed(manifest))
                with archive.open('reviewed-rows.csv','w',force_zip64=True) as output:
                    text = io.TextIOWrapper(output,encoding='utf-8-sig',newline='')
                    writer = csv.writer(text)
                    writer.writerow(['source_row','line_start','line_end','group_id','review_status']+json.loads(r['headers']))
                    cursor = db.execute('''SELECT r.*,m.group_id,g.disposition FROM import_rows r
                        LEFT JOIN group_members m ON m.import_id=r.import_id AND m.generation=? AND m.row_no=r.row_no
                        LEFT JOIN review_groups g ON g.import_id=m.import_id AND g.generation=m.generation AND g.group_id=m.group_id
                        WHERE r.import_id=? ORDER BY r.row_no''',(r['generation'],ident))
                    for row in bounded(cursor):
                        writer.writerow([row['row_no'],row['line_start'],row['line_end'],row['group_id'] or '',row['disposition'] or 'single_row']+[safe_cell(x) for x in json.loads(row['cells'])])
                    text.flush();text.detach()
                with archive.open('evidence.json','w',force_zip64=True) as out:
                    out.write(b'{"rows":')
                    write_array(out,bounded(db.execute('SELECT * FROM import_rows WHERE import_id=? ORDER BY row_no',(ident,)),wb.row))
                    out.write(b',"groups":')
                    write_array(out,bounded(db.execute('''SELECT import_id,generation,group_id,key_value,kind,row_count,evidence_sha256,evidence_format
                        FROM review_groups WHERE import_id=? AND generation=? ORDER BY group_id''',(ident,r['generation']))))
                    out.write(b',"issues":')
                    write_array(out,bounded(db.execute('SELECT * FROM row_issues WHERE import_id=? AND generation=? ORDER BY row_no',(ident,r['generation']))))
                    out.write(b'}')
                with archive.open('group-members.jsonl','w',force_zip64=True) as out:
                    last, group_hash, expected, algorithm = None,None,None,None
                    verified_groups = 0
                    cursor = db.execute('''SELECT r.*,m.group_id,g.evidence_sha256,g.evidence_format FROM group_members m
                        JOIN import_rows r ON r.import_id=m.import_id AND r.row_no=m.row_no
                        JOIN review_groups g ON g.import_id=m.import_id AND g.generation=m.generation AND g.group_id=m.group_id
                        WHERE m.import_id=? AND m.generation=? ORDER BY m.group_id,m.row_no''',(ident,r['generation']))
                    def finish_group():
                        if algorithm=='legacy-json-array-sha256':
                            group_hash.update(b']')
                        if group_hash.hexdigest()!=expected:
                            raise WorkflowError('evidence_hash_mismatch',422)
                    for row in bounded(cursor):
                        gid = row['group_id']
                        if gid!=last:
                            if last is not None:
                                finish_group()
                            group_hash,last,expected,algorithm = hashlib.sha256(),gid,row['evidence_sha256'],row['evidence_format']
                            verified_groups += 1
                            if algorithm=='legacy-json-array-sha256':
                                group_hash.update(b'[')
                            first = True
                        source = wb.row(row)
                        if algorithm=='legacy-json-array-sha256':
                            if not first:
                                group_hash.update(b',')
                            group_hash.update(packed(source).encode())
                        else:
                            group_hash.update((hashlib.sha256(packed(source).encode()).hexdigest()+'\n').encode())
                        first = False
                        out.write((packed({'group_id':gid,'row_no':row['row_no']})+'\n').encode())
                    if last is not None:
                        finish_group()
                    if verified_groups!=r['group_count']:
                        raise WorkflowError('evidence_hash_mismatch',422)
                with archive.open('review-history.json','w',force_zip64=True) as out:
                    write_array(out,bounded(db.execute('SELECT body FROM review_events WHERE import_id=? ORDER BY revision',(ident,)),lambda x:json.loads(x[0])))
            with partial.open('r+b') as stream:
                os.fsync(stream.fileno())
            os.rename(partial,target)
            with target.open('rb') as stream:
                checksum = hashlib.file_digest(stream,'sha256').hexdigest()
            target.with_suffix('.sha256').write_text(checksum,encoding='ascii')
        return target
