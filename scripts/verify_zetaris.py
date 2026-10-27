"""Verify original imports offline, or accept a real Zetaris tenant. Never uses mocks."""
import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values
from supplytrace.adapters import ZetarisSource, digest, read_evidence
from supplytrace.config import Settings
from supplytrace.models import CheckRequest, Plan, ROW_MODELS, TOOLS, ToolCall, WorkflowError
from supplytrace.verifier import verify


def audit_imports(data_dir):
    original = json.loads((data_dir / 'synthetic.json').read_text(encoding='utf8'))
    manifest = []
    for tool in TOOLS:
        path = data_dir / 'zetaris-import' / f'{tool}.csv'
        model = ROW_MODELS[tool]
        with path.open(encoding='utf-8-sig', newline='') as stream:
            reader = csv.DictReader(stream)
            if set(reader.fieldnames or []) != set(model.model_fields):
                raise ValueError(f'{tool}: import columns mismatch')
            typed = []
            for raw in reader:
                row = {k: int(v) if model.model_fields[k].annotation is int else v for k, v in raw.items()}
                typed.append(model.model_validate(row).model_dump(mode='json'))
        expected = [model.model_validate(r).model_dump(mode='json') for r in original[tool]]
        if sorted(typed, key=lambda r: r['id']) != sorted(expected, key=lambda r: r['id']):
            raise ValueError(f'{tool}: CSV does not match original synthetic data')
        if len({r['id'] for r in typed}) != len(typed):
            raise ValueError(f'{tool}: duplicate row ID')
        manifest.append({'tool': tool, 'csv': str(path.relative_to(data_dir)),
                         'rows': len(typed), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                         'columns': list(model.model_fields),
                         'integer_columns': [k for k, v in model.model_fields.items() if v.annotation is int],
                         'lamp01_ids': [r['id'] for r in typed if r['sku'] == 'LAMP-01'],
                         'original_match': True})
    return original, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--offline', action='store_true')
    modes.add_argument('--live', action='store_true')
    parser.add_argument('--env-file',type=Path,default=ROOT/'.env')
    parser.add_argument('--output-dir',type=Path,default=ROOT/'runtime')
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    report = {'checked_at': datetime.now(timezone.utc).isoformat(),
              'mode': 'live' if args.live else 'offline', 'official_platform_passed': False,
              'http_attempted': False, 'traces': [], 'synthetic_data': True}
    code = 0
    try:
        original, report['import_manifest'] = audit_imports(ROOT / 'data')
        report['offline_import_audit_passed'] = True
        if args.live:
            # Only this project's configuration. Never inherit another worker's credentials.
            values = dotenv_values(args.env_file, interpolate=False)
            settings = Settings(zetaris_org=values.get('ZETARIS_ORG_ID') or '',
                                zetaris_key=values.get('ZETARIS_API_KEY') or '',
                                zetaris_base=values.get('ZETARIS_BASE_URL') or 'https://api.saas.enterprise.zetaris.com',
                                zetaris_catalog=values.get('ZETARIS_CATALOG') or 'supplytrace',
                                zetaris_prefix=values.get('ZETARIS_API_PREFIX') or '/api/v1.0',
                                zetaris_username=values.get('ZETARIS_USERNAME') or '',
                                zetaris_password=values.get('ZETARIS_PASSWORD') or '')
            if not ((settings.zetaris_org and settings.zetaris_key) or (settings.zetaris_username and settings.zetaris_password)):
                raise WorkflowError('blocked_missing_tenant_configuration')
            plan = Plan(rationale='Read all four original imported sources and verify real pagination and cursor cleanup.',
                        queries=[ToolCall(tool=tool, sku='LAMP-01') for tool in TOOLS])
            source = ZetarisSource(settings, page_limit=1)
            report['http_attempted'] = True
            rows, _ = read_evidence(source, plan, report['traces'])
            for tool in TOOLS:
                actual = [ROW_MODELS[tool].model_validate(r).model_dump(mode='json') for r in rows[tool]]
                expected = [r for r in original[tool] if r['sku'] == 'LAMP-01']
                if digest(sorted(actual, key=lambda r: r['id'])) != digest(sorted(expected, key=lambda r: r['id'])):
                    raise WorkflowError('zetaris_import_mismatch')
            for trace in report['traces']:
                if trace['pages'] != trace['row_count'] or trace['cursor'] != 'closed':
                    raise WorkflowError('zetaris_paging_acceptance_failed')
            request = CheckRequest(sku='LAMP-01', quantity=30, as_of='2026-10-22', promised_date='2026-10-28', mode='rules')
            report['decision'] = verify(request, rows)
            if report['decision']['capacity'] != 55 or report['decision']['verdict'] != 'can_promise':
                raise WorkflowError('zetaris_verdict_acceptance_failed')
            report['official_platform_passed'] = True
            report['status'] = 'live_four_sources_pagination_and_close_passed'
        else:
            report['status'] = 'offline_import_audit_passed_not_platform_acceptance'
    except WorkflowError as error:
        report['status'] = error.code
        code = 2
    except (ValueError, OSError):
        report['status'] = 'data_or_configuration_validation_failed'
        code = 1
    output = args.output_dir / f'zetaris-{report["mode"]}-{stamp}.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({'artifact': str(output), 'status': report['status'],
                      'official_platform_passed': report['official_platform_passed'],
                      'http_attempted': report['http_attempted']}, ensure_ascii=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
