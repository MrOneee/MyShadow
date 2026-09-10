import sqlite3,time,unittest
from unittest.mock import Mock
import test_poll_changes
from bot import Bot,prompt_for,table_for
from conversations import direct_contacts,direct_id


class DirectMessagesTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_poll_changes.PollChangesTests()
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        f=self.fixture;self.bot=f.bot
        c=f.fixtures[f.contact]
        for name,kind,default in [('remark','TEXT',"''"),('local_type','INTEGER','2'),('flag','INTEGER','3'),
                                   ('delete_flag','INTEGER','0'),('verify_flag','INTEGER','0')]:
            c.execute(f'ALTER TABLE contact ADD COLUMN {name} {kind} DEFAULT {default}')
        for user,name,kind,flags,deleted,verified in [
            ('wxid_a','Alice',1,3,0,0),('wxid_b','Bob',1,2051,0,0),
            ('wxid_stranger','Stranger',3,4,0,0),('filehelper','Files',1,3,0,0),
            ('gh_public','Public',1,3,0,0),('wxid_deleted','Deleted',1,3,1,0),
            ('wxid_blocked','Blocked',1,11,0,0),('weixin','WeChat',1,3,0,56)]:
            c.execute('INSERT INTO contact(username,nick_name,local_type,flag,delete_flag,verify_flag) VALUES(?,?,?,?,?,?)',
                      (user,name,kind,flags,deleted,verified))
        c.commit()
        self.bot.config['private_messages']={'enabled':True,'enabled_since':int(time.time())-10}
        db=f.fixtures[f.shard]
        db.execute("INSERT INTO Name2Id VALUES('wxid_b')")
        for user in ('wxid_a','wxid_b'):
            db.execute('CREATE TABLE '+table_for(user)+'(local_id INTEGER PRIMARY KEY,server_id INTEGER,local_type INTEGER,'
                       'real_sender_id INTEGER,create_time INTEGER,source TEXT,message_content TEXT)')
        db.commit()

    def incoming(self,user,text,sender=1,age=0,kind=1):
        f=self.fixture;c=f.fixtures[f.shard];table=table_for(user)
        ident=c.execute('SELECT coalesce(max(local_id),0)+1 FROM '+table).fetchone()[0]
        c.execute('INSERT INTO '+table+' VALUES(?,?,?,?,?,?,?)',(ident,ident,kind,sender,int(time.time())-age,'',text))
        c.commit();f.revisions[f.shard]=('message',time.monotonic())

    def test_only_friends_discovered(self):
        with self.fixture.open_snapshot(self.fixture.contact) as c:
            self.assertEqual(set(direct_contacts(c,self.bot.config)),{'wxid_a','wxid_b'})
        self.assertFalse(direct_id('../secret'));self.assertFalse(direct_id('gh_public'))

    def test_private_plain_text_and_same_ids_are_separate(self):
        self.incoming('wxid_a','你好');self.incoming('wxid_b','另一段话',sender=3)
        self.assertEqual(self.bot.poll(),{})
        self.assertEqual(set(self.bot.state.execute('SELECT group_id,prompt FROM replies')),
                         {('wxid_a','你好'),('wxid_b','另一段话')})

    def test_self_unknown_sender_system_and_old_messages_ignored(self):
        self.incoming('wxid_a','自己',sender=2)
        self.incoming('wxid_a','别人的消息',sender=3)
        self.incoming('wxid_a','启用前',age=20)
        self.incoming('wxid_a','系统通知',kind=10000)
        self.bot.poll();self.assertEqual(self.bot.state.execute('SELECT count(*) FROM replies').fetchone()[0],0)

    def test_disabled_does_not_scan_private(self):
        self.bot.config['private_messages']['enabled']=False
        self.incoming('wxid_a','你好');self.bot.poll()
        self.assertNotIn('wxid_a',self.bot.groups)
        self.assertEqual(self.bot.state.execute('SELECT count(*) FROM replies').fetchone()[0],0)

    def test_restart_does_not_repeat_reply(self):
        self.incoming('wxid_a','你好');self.bot.poll()
        self.bot._contact_scan=None;self.bot._message_scans={};self.bot.poll()
        self.assertEqual(self.bot.state.execute('SELECT count(*) FROM replies').fetchone()[0],1)

    def test_renamed_or_deleted_contact_blocks_send(self):
        self.bot.poll();c=self.fixture.fixtures[self.fixture.contact]
        c.execute("UPDATE contact SET delete_flag=1 WHERE username='wxid_a'");c.commit()
        with self.fixture.open_snapshot(self.fixture.contact) as snapshot:
            with self.assertRaises(RuntimeError):self.bot.verify_group('wxid_a',snapshot)

    def test_duplicate_contact_title_blocks_send(self):
        c=self.fixture.fixtures[self.fixture.contact]
        c.execute("UPDATE contact SET nick_name='Alice' WHERE username='wxid_b'");c.commit()
        self.assertIn('wxid_a',self.bot.poll())

    def test_quotes_and_images_without_mention(self):
        self.incoming('wxid_a','<msg><appmsg><type>57</type><title>你怎么看</title></appmsg></msg>',kind=49)
        self.incoming('wxid_a','image',kind=3)
        self.bot.poll()
        self.assertEqual([r[0] for r in self.bot.state.execute('SELECT prompt FROM replies ORDER BY id')],
                         ['你怎么看','请看看这张图片。'])

    def test_private_system_context_is_explicit(self):
        self.bot.config['system_prompt']='你的名字是影。'
        self.assertIn('一对一私聊',self.bot.system_prompt_for('wxid_a'))
        self.assertNotIn('一对一私聊',self.bot.system_prompt_for('111@chatroom'))
