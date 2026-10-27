"""Reject ambiguous source identities without deleting or choosing source rows."""
from .adapters import digest
from .models import WorkflowError


def validate_identities(tool, rows):
    """Row numbers are one-based within the unmodified source response.

    An ID is a source contract, not a deduplication hint. Different content can
    mean a bad join/key or competing versions; only the source owner can decide.
    """
    if not isinstance(rows,list):raise WorkflowError('source_schema_invalid',422)
    seen={};conflicts=[]
    for number,row in enumerate(rows,1):
        if not isinstance(row,dict) or not isinstance(row.get('id'),str) or not 1<=len(row['id'])<=80:
            raise WorkflowError('source_schema_invalid',422)
        identity=row['id']
        if identity not in seen:
            seen[identity]=(number,row);continue
        previous,original=seen[identity]
        hashes=[digest(original),digest(row)]
        changed=sorted(k for k in original.keys()|row.keys()
                       if k not in original or k not in row or digest(original[k])!=digest(row[k]))
        conflicts.append({'id':identity,'ref':f'{tool}:{identity}',
            'kind':'conflict' if changed else 'duplicate','row_numbers':[previous,number],
            'changed_fields':changed,'row_sha256':hashes})
    if conflicts:
        code='source_identity_conflict' if any(c['kind']=='conflict' for c in conflicts) else 'duplicate_evidence'
        raise WorkflowError(code,422,{'tool':tool,'identity_key':'id','conflicts':conflicts,
            'row_number_scope':'one-based positions in this source response; raw rows retained',
            'resolution':'source_owner_required','auto_resolved':False})
