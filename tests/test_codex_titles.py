import copy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from aw.adapters import codex
from aw.adapters.codex_titles import read_titles, user_text
from aw.config import DEFAULTS


class CodexTitleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='codex names ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/'sessions').mkdir()
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg['adapters']['codex']['root'] = str(self.root)

    def database(self, rows, filename='state_5.sqlite', named=True):
        path = self.root/filename
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as con, con:
            con.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT'+(', name TEXT' if named else '')+')')
            con.executemany('INSERT INTO threads VALUES ('+','.join('?' for _ in range(3 if named else 2))+')', rows)
        return path

    def index(self, rows):
        (self.root/'session_index.jsonl').write_text('\n'.join(json.dumps(r) for r in rows)+'\n', encoding='utf-8')

    def rollout(self, sid='main', prompts=('Build a dashboard', 'Yes'), **meta):
        rows=[{'type':'session_meta','payload':{'id':sid,'source':'vscode',**meta}}]
        rows += [{'type':'event_msg','timestamp':'2026-10-28T12:00:00Z','payload':{'type':'user_message','message':p}} for p in prompts]
        rows.append({'type':'event_msg','timestamp':'2026-10-28T12:00:01Z','payload':{'type':'task_complete'}})
        (self.root/'sessions'/f'rollout-{sid}.jsonl').write_text('\n'.join(json.dumps(r) for r in rows)+'\n', encoding='utf-8')

    def test_actual_name_overrides_first_and_latest_message_and_stale_index(self):
        self.database([('main','Build a dashboard','My monitoring project')])
        self.index([{'id':'main','thread_name':'Old name'}])
        self.rollout()
        task=codex.scan(self.cfg)[0]
        self.assertEqual(task.title,'My monitoring project')
        self.assertEqual(task.prompt,'Yes')
        self.assertEqual(task.extra['title_source'],'codex_name')

    def test_db_read_only_and_rename_visible_on_next_scan(self):
        path=self.database([('main','Initial question','Original name')])
        self.rollout()
        digest=hashlib.sha256(path.read_bytes()).digest()
        self.assertEqual(codex.scan(self.cfg)[0].title,'Original name')
        self.assertEqual(hashlib.sha256(path.read_bytes()).digest(),digest)
        with closing(sqlite3.connect(path)) as con, con:con.execute("UPDATE threads SET name='Renamed project'")
        self.assertEqual(codex.scan(self.cfg)[0].title,'Renamed project')

    def test_index_is_used_with_old_schema_and_latest_valid_entry_wins(self):
        self.database([('main','First question')],named=False)
        self.index([{'id':'main','thread_name':'Original'},[],{'id':'main','thread_name':'New name'}, {'id':'unrelated','thread_name':'Ignored'}])
        with (self.root/'session_index.jsonl').open('a') as f:f.write('{"id":')
        self.assertEqual(read_titles(str(self.root),['main']),{'main':{'title':'New name','source':'session_index'}})

    def test_new_schema_version_and_sqlite_subdirectory(self):
        self.database([('main','First question','Old')])
        self.database([('main','First question','New')],filename='sqlite/state_12.sqlite')
        self.assertEqual(read_titles(str(self.root),['main'])['main']['title'],'New')

    def test_missing_corrupt_and_locked_db_do_not_prevent_index_fallback(self):
        self.index([{'id':'main','thread_name':'Index name'}])
        self.assertEqual(read_titles(str(self.root),['main'])['main']['title'],'Index name')
        (self.root/'state_5.sqlite').write_text('not SQLite')
        self.assertEqual(read_titles(str(self.root),['main'])['main']['title'],'Index name')
        with patch('aw.adapters.codex_titles.sqlite3.connect', side_effect=sqlite3.OperationalError('database is locked')):
            self.assertEqual(read_titles(str(self.root),['main'])['main']['title'],'Index name')

    def test_unsupported_schema_falls_back_without_creating_databases(self):
        with closing(sqlite3.connect(self.root/'state_5.sqlite')) as con, con:con.execute('CREATE TABLE unrelated (x TEXT)')
        before=set(self.root.iterdir())
        self.assertEqual(read_titles(str(self.root),['main']),{})
        self.assertEqual(set(self.root.iterdir()),before)

    def test_old_db_title_strips_attachment_wrapper(self):
        wrapper='# Files mentioned by the user:\n\n## photo.png: /private/photo.png\n\n## My request:\nDesign a camera cover'
        self.database([('main',wrapper)],named=False)
        self.assertEqual(read_titles(str(self.root),['main'])['main']['title'],'Design a camera cover')

    def test_no_metadata_uses_stable_first_request_not_acknowledgement(self):
        self.rollout(prompts=('<environment_context>setup</environment_context>','Build a dashboard','Yes','<external_codex_apps_open_page>{}</external_codex_apps_open_page>'))
        task=codex.scan(self.cfg)[0]
        self.assertEqual((task.title,task.prompt),('Build a dashboard','Yes'))
        self.assertEqual(task.extra['title_source'],'first_user_message')

    def test_rollout_name_used_when_index_and_database_unavailable(self):
        self.rollout(name='Saved conversation name')
        self.assertEqual(codex.scan(self.cfg)[0].title,'Saved conversation name')

    def test_long_unicode_name_is_preserved(self):
        name='为厨房水池设计收纳装置并验证尺寸与打印设置'*4
        self.database([('main','First question',name)])
        self.rollout()
        self.assertEqual(codex.scan(self.cfg)[0].title,name)

    def test_names_also_resolve_for_folded_children(self):
        self.rollout()
        self.rollout('child',source={'subagent':{'thread_spawn':{'parent_thread_id':'main'}}})
        self.database([('main','Main question','Main task'),('child','Child question','Code review')])
        tasks=codex.scan(self.cfg)
        self.assertEqual(len(tasks),1)
        self.assertEqual(tasks[0].extra['subagents'][0]['label'],'Code review')

    def test_title_reads_are_batched_for_large_installations(self):
        rows=[(str(i),'Question','Task '+str(i)) for i in range(1005)]
        self.database(rows)
        self.assertEqual(len(read_titles(str(self.root),[r[0] for r in rows])),len(rows))

    def test_response_item_wrapper_and_question_reply_are_cleaned(self):
        rows=[{'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'# Files mentioned by the user:\n\nfile\n\n## My request:\nFix the hinge'}]}}]
        reply='<send_user_message_question_reply>[{"answer":"Use metal"}]</send_user_message_question_reply>'
        rows.append({'type':'event_msg','payload':{'type':'user_message','message':reply}})
        state=codex.analyze(rows,1800000000,DEFAULTS)
        self.assertEqual(state['first_prompt'],'Fix the hinge')
        self.assertEqual(state['prompt'],'Use metal')

    def test_only_known_context_envelopes_removed(self):
        self.assertEqual(user_text('<in-app-browser-context source="ambient">page</in-app-browser-context>\nActual request'),'Actual request')
        self.assertEqual(user_text('<environment_context>setup</environment_context>'),'')
        self.assertEqual(user_text('# Files mentioned by the user:\nfile'),'')
        self.assertEqual(user_text('# AGENTS.md instructions for /project\nsetup'),'')
        self.assertEqual(user_text('<example>Keep this text</example>'),'<example>Keep this text</example>')
