"""Bounded CSV records and Arrow -> DuckDB external grouping.

Source strings never undergo inferred conversion. Decimal is a comparison token,
not a replacement value. CSV physical line positions remain authoritative.
"""
import csv
import hashlib
import io
import json
import time
from decimal import Decimal, InvalidOperation

import duckdb
import pyarrow as pa

from .models import WorkflowError

MAX_ROWS = 500_000
MAX_BYTES = 64 * 1024 * 1024
MAX_FIELD = 32768
MAX_RECORD = 262144
BATCH_ROWS = 512
BATCH_BYTES = 1024 * 1024
csv.field_size_limit(MAX_FIELD)


def packed(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


class BlobReader(io.RawIOBase):
    def __init__(self, blob):
        self.blob = blob

    def readable(self):
        return True

    def readinto(self, buffer):
        data = self.blob.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)


class LimitedLines:
    def __init__(self, stream):
        self.stream, self.record_size, self.line = stream, 0, 0

    def __iter__(self):
        return self

    def __next__(self):
        value = self.stream.readline(MAX_RECORD + 1)
        if not value:
            raise StopIteration
        self.line += 1
        self.record_size += len(value)
        if self.record_size > MAX_RECORD:
            raise WorkflowError('record_too_large', 422, {'line': self.line})
        if '\x00' in value:
            raise WorkflowError('binary_file', 422, {'line': self.line})
        return value


def records(binary, encoding, delimiter, max_rows=MAX_ROWS):
    if encoding not in ('utf-8-sig', 'gb18030') or delimiter not in (',', ';', '\t'):
        raise WorkflowError('invalid_csv_options', 422)
    text = io.TextIOWrapper(binary, encoding=encoding, newline='')
    lines = LimitedLines(text)
    reader = csv.reader(lines, delimiter=delimiter, strict=True)
    try:
        headers = next(reader)
        if not 2 <= len(headers) <= 100 or any(not h.strip() or len(h) > 200 for h in headers) or len(set(headers)) != len(headers):
            raise WorkflowError('invalid_headers', 422, {'line': 1})
        yield headers
        count = 0
        while True:
            start = reader.line_num + 1
            lines.record_size = 0
            try:
                cells = next(reader)
            except StopIteration:
                break
            count += 1
            if count > max_rows:
                raise WorkflowError('row_limit', 422, {'line': start})
            yield {'row_no': count, 'line_start': start, 'line_end': reader.line_num,
                   'cells': cells, 'error': 'column_count' if len(cells) != len(headers) else None}
        if not count:
            raise WorkflowError('empty_csv', 422)
    except StopIteration:
        raise WorkflowError('empty_csv', 422)
    except UnicodeError as exc:
        # TextIO may decode ahead of readline. Locate the offending physical line
        # within that bounded decoder block instead of blaming the last yielded row.
        prefix = exc.object[:exc.start] if isinstance(exc,UnicodeDecodeError) else b''
        breaks = prefix.count(b'\n')+prefix.count(b'\r')-prefix.count(b'\r\n')
        raise WorkflowError('encoding_error', 422, {'line': lines.line+breaks+1})
    except csv.Error:
        raise WorkflowError('malformed_csv', 422, {'line': max(1, lines.line)})
    finally:
        text.detach()  # caller owns the blob/connection


def batches(iterable, size=BATCH_ROWS):
    chunk, count = [], 0
    for item in iterable:
        # Conservative string-size bound in addition to row bound.
        weight = len(packed(item).encode('utf-8'))
        if chunk and (len(chunk) >= size or count + weight > BATCH_BYTES):
            yield chunk
            chunk, count = [], 0
        chunk.append(item)
        count += weight
    if chunk:
        yield chunk


def quantity_token(value):
    try:
        number = Decimal(value.strip())
        if not number.is_finite():
            raise InvalidOperation
        sign, digits, exponent = number.as_tuple()
        digits = list(digits)
        while digits and digits[-1] == 0:
            digits.pop()
            exponent += 1
        if not digits:
            return '0', True
        return f'{sign}:{"".join(map(str, digits))}:{exponent}', True
    except InvalidOperation:
        return 'invalid:' + value, False


class Analysis:
    """One worker, one disk-backed engine. No Python list of groups or members."""
    def __init__(self, path):
        self.db = duckdb.connect(str(path), config={'memory_limit': '128MB', 'threads': '1',
            'preserve_insertion_order': 'false', 'max_temp_directory_size': '1GB',
            'enable_external_access': 'false'})
        self.db.execute('CREATE TABLE facts(row_no BIGINT, key_value VARCHAR, qty VARCHAR, content_hash VARCHAR, row_hash VARCHAR)')

    def append(self, rows, headers, mapping):
        keys = [headers.index(k) for k in mapping['keys']]
        quantity = headers.index(mapping['quantity'])
        facts = {'row_no': [], 'key_value': [], 'qty': [], 'content_hash': [], 'row_hash': []}
        issues = []
        for row in rows:
            cells, n = row['cells'], row['row_no']
            if row['parse_error']:
                issues.append((n, '', 'column_count'))
                continue
            key = [cells[k] for k in keys]
            token, valid = quantity_token(cells[quantity])
            if not valid:
                issues.append((n, mapping['quantity'], 'invalid_quantity'))
            key_value = packed(key)
            if any(not k.strip() for k in key):
                issues.append((n, ', '.join(mapping['keys']), 'blank_key'))
                continue
            if len(key_value.encode()) > 8192:
                issues.append((n, ', '.join(mapping['keys']), 'key_too_large'))
                continue
            facts['row_no'].append(n)
            facts['key_value'].append(key_value)
            facts['qty'].append(token)
            facts['content_hash'].append(hashlib.sha256(packed(cells).encode()).hexdigest())
            facts['row_hash'].append(hashlib.sha256(packed(row).encode()).hexdigest())
        if facts['row_no']:
            table = pa.table(facts)
            self.db.register('arrow_chunk', table)
            try:
                self.db.execute('INSERT INTO facts SELECT * FROM arrow_chunk')
            finally:
                self.db.unregister('arrow_chunk')
        return issues

    def group(self):
        # min/max are constant-state aggregates even when all 500k rows share a key.
        self.db.execute('''CREATE TABLE grouped AS SELECT row_number() OVER(ORDER BY first_row) AS group_id, * FROM (
          SELECT key_value, min(row_no) first_row, count(*) row_count,
          CASE WHEN min(qty) != max(qty) THEN 'quantity_difference'
               WHEN min(content_hash) = max(content_hash) THEN 'identical_content'
               ELSE 'multiple_rows' END kind
          FROM facts GROUP BY key_value HAVING count(*)>1)''')

    def stream(self, sql):
        cursor = self.db.execute(sql)
        while chunk := cursor.fetchmany(BATCH_ROWS):
            yield chunk

    def close(self):
        self.db.close()
