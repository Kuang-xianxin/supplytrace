"""Bounded local-model help for one visible evidence page; never writes a review."""
import json
import os
import threading
import uuid

from .assistant import SYSTEM, suggest
from .models import WorkflowError
from .storage import now
from .workspace import fingerprint


class ReviewAssistant:
    def __init__(self, workbench, settings, transport=None):
        self.workbench, self.settings, self.transport = workbench, settings, transport
        self.slot = threading.BoundedSemaphore(1)
        self.directory = workbench.directory / 'assistant-evidence'
        self.directory.mkdir(exist_ok=True)

    def investigate(self, ident, gid, body):
        if not self.settings.model:
            raise WorkflowError('model_not_configured', 503)
        if not self.slot.acquire(blocking=False):
            raise WorkflowError('assistant_busy', 429)
        try:
            detail = self.workbench.detail(ident, gid, body.offset)
            if body.expected_revision != detail['revision']:
                raise WorkflowError('review_stale_reload_required', 409)
            evidence = {'headers': detail['headers'], 'rows': detail['rows']}
            if not evidence['rows']:
                raise WorkflowError('group_not_found', 404)
            identity = {'import_id': ident, 'group_id': gid, 'offset': body.offset,
                        'evidence_sha256': detail['evidence_sha256'],
                        'page_sha256': fingerprint(evidence), 'locale': body.locale,
                        'model': self.settings.model, 'prompt_sha256': fingerprint(SYSTEM)}
            target = self.directory / (fingerprint(identity) + '.json')
            if target.is_file():
                result = json.loads(target.read_text(encoding='utf-8'))
                return {**result, 'cached': True}
            # Local calls are still a finite resource. Fail visibly before inference.
            today = now()[:10]
            used = sum(1 for p in self.directory.glob('*.json')
                       if json.loads(p.read_text(encoding='utf-8')).get('created_at', '').startswith(today))
            if used >= self.settings.daily_limit:
                raise WorkflowError('daily_quota_exceeded', 429)
            self.workbench.disk_guard(131072)
            output = suggest(self.settings.base_url, self.settings.model, evidence,
                             body.locale, self.transport)
            result = {**identity, **output, 'created_at': now(), 'cached': False,
                      'review_modified': False, 'original_rows_modified': False,
                      'shown_rows': len(detail['rows']), 'group_rows': detail['total'],
                      'scope': 'Only the displayed source rows; suggestions require human verification.'}
            if result['status'] == 'failed':
                # Preserve failed attempts too; they count against today's local quota.
                target = self.directory / ('failed-' + uuid.uuid4().hex + '.json')
            partial = target.with_suffix('.' + uuid.uuid4().hex + '.partial')
            with partial.open('x', encoding='utf-8') as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.rename(partial, target)
            return result
        finally:
            self.slot.release()
