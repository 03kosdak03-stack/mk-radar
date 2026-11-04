import io,json,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
import app

class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.old=app.DATA;app.DATA=Path(self.tmp.name);app.init()
 def tearDown(self):
  app.DATA=self.old;self.tmp.cleanup()
 def blob(self, data=None, name='exports/web/final_snapshot.json'):
  out=io.BytesIO()
  with zipfile.ZipFile(out,'w') as z:z.writestr(name,json.dumps(data or app.current()[app.NAMES[0]]))
  return out.getvalue()
 def test_valid_and_persistence(self):
  app.import_zip(self.blob());app.init();self.assertEqual(app.current()[app.NAMES[0]]['asof'],'2026-10-23')
 def test_invalid_preserves(self):
  before=app.current();d=json.loads(json.dumps(before[app.NAMES[0]]));d['asset'][0]['price']=-1
  with self.assertRaises(ValueError):app.import_zip(self.blob(d))
  self.assertEqual(app.current(),before)
 def test_old_rejected(self):
  d=app.current()[app.NAMES[0]];d['asof']='2020-01-01'
  with self.assertRaises(ValueError):app.import_zip(self.blob(d))
 def test_path_rejected(self):
  with self.assertRaises(ValueError):app.import_zip(self.blob(name='../final_snapshot.json'))
 def test_no_code_execution(self):
  b=io.BytesIO()
  with zipfile.ZipFile(b,'w') as z:
   z.writestr('app.py','raise SystemExit()');z.writestr('final_snapshot.json',json.dumps(app.current()[app.NAMES[0]]))
  app.import_zip(b.getvalue());self.assertFalse((app.DATA/'app.py').exists())
 def test_duplicate_rejected(self):
  b=io.BytesIO();d=app.current()[app.NAMES[0]]
  with zipfile.ZipFile(b,'w') as z:
   z.writestr('a/final_snapshot.json',json.dumps(d));d['asof']='2020-01-01';z.writestr('b/final_snapshot.json',json.dumps(d))
  with self.assertRaises(ValueError):app.import_zip(b.getvalue())
 def test_unauthorized_no_download(self):
  with patch.object(app,'ADMIN',123),patch.object(app,'send'),patch.object(app,'api') as api:
   app.handle({'chat':{'type':'private','id':999},'from':{'id':999},'document':{'file_name':'x.zip'}});api.assert_not_called()
 def test_optional_not_stale(self):
  app.import_zip(self.blob());self.assertNotIn('final_backtest.json',app.current())
 def test_commands(self):
  with patch.object(app,'ADMIN',123),patch.object(app,'send') as send:
   for cmd in ['/start','/durum','/secim','/varlik','/faaliyet','/deger IHGZT','/takip','/mkbacktest','/kural']:
    app.handle({'chat':{'type':'private','id':123},'from':{'id':123},'text':cmd})
   self.assertEqual(send.call_count,9)
if __name__=='__main__':unittest.main()
