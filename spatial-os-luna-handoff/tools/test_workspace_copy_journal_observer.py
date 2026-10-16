"""Real SQLite contention checks for the fixture observer. Run on sb16/sb16-2."""
import ast
from contextlib import closing
from pathlib import Path
import socket
import sqlite3
import tempfile
import threading
import time
import unittest

SOURCE=Path(__file__).resolve().parents[1]/'tests/core/test_workspace_copy_faults.py'
module=ast.parse(SOURCE.read_text())
function=next(node for node in module.body if isinstance(node,ast.FunctionDef) and node.name=='read_copy_terminal_before_deadline')
namespace=dict(closing=closing,sqlite3=sqlite3,time=time)
exec(compile(ast.Module(body=[function],type_ignores=[]),str(SOURCE),'exec'),namespace)
observe=namespace['read_copy_terminal_before_deadline']


class CopyJournalObserverTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory(prefix='copy-observer-sqlite-')
        self.addCleanup(self.directory.cleanup)
        self.database=Path(self.directory.name)/'observer.db'
        # Arbitrary rows exercise SQLite reads. They are not production world state.
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('PRAGMA journal_mode=DELETE')
            connection.execute('CREATE TABLE copy_operations(operation_id BLOB,phase INTEGER,error INTEGER,destination TEXT)')
            connection.execute('INSERT INTO copy_operations VALUES(?,?,?,?)',(b'row-key',101,202,'before'))

    @staticmethod
    def audit():return dict(count=0,first=None,last=None,last_error=None)

    def test_exclusive_writer_release_allows_committed_read(self):
        audit=self.audit();outcome={};deadline=time.monotonic_ns()+2_000_000_000
        def read():
            try:outcome['row']=observe(self.database,b'row-key',deadline,audit)
            except BaseException as error:outcome['error']=error
        with closing(sqlite3.connect(self.database)) as writer, writer:
            writer.execute('BEGIN EXCLUSIVE')
            writer.execute('UPDATE copy_operations SET destination=?',('committed',))
            worker=threading.Thread(target=read);worker.start()
            try:
                until=time.monotonic()+1
                while not audit['count'] and worker.is_alive() and time.monotonic()<until:time.sleep(.005)
                self.assertGreaterEqual(audit['count'],1,'Reader did not witness the real exclusive lock')
                writer.commit()
            finally:
                writer.rollback();worker.join(timeout=3)
        self.assertFalse(worker.is_alive());self.assertNotIn('error',outcome)
        self.assertEqual(outcome['row'],(101,202,'committed'))
        self.assertEqual(audit['first']['sqlite_errorcode'],sqlite3.SQLITE_BUSY)
        self.assertLessEqual(audit['first']['returned_monotonic_ns'],audit['last']['returned_monotonic_ns'])

    def test_persistent_busy_expires_the_original_absolute_deadline(self):
        audit=self.audit();outcome={};deadline=time.monotonic_ns()+250_000_000
        def read():
            try:outcome['row']=observe(self.database,b'row-key',deadline,audit)
            except BaseException as error:outcome.update(error=error,returned=time.monotonic_ns())
        with closing(sqlite3.connect(self.database)) as writer, writer:
            writer.execute('BEGIN EXCLUSIVE')
            worker=threading.Thread(target=read);worker.start()
            try:
                worker.join(timeout=1)
                overran=worker.is_alive()
            finally:
                writer.rollback();worker.join(timeout=3)
        self.assertFalse(worker.is_alive());self.assertFalse(overran,'Observer reset its absolute deadline')
        self.assertIsInstance(outcome.get('error'),TimeoutError)
        self.assertNotIn('row',outcome)
        self.assertGreaterEqual(audit['count'],1)
        self.assertGreaterEqual(outcome['returned'],deadline)
        self.assertLess(outcome['returned']-deadline,750_000_000)

    def test_different_sqlite_error_propagates_without_busy_retry(self):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('DROP TABLE copy_operations')
        audit=self.audit();started=time.monotonic_ns()
        with self.assertRaises(sqlite3.OperationalError) as caught:
            observe(self.database,b'row-key',started+2_000_000_000,audit)
        self.assertEqual(caught.exception.sqlite_errorcode,sqlite3.SQLITE_ERROR)
        self.assertEqual(audit['count'],0)
        self.assertEqual(audit['last_error']['sqlite_errorcode'],sqlite3.SQLITE_ERROR)
        self.assertLess(time.monotonic_ns()-started,1_000_000_000)


if __name__=='__main__':
    if socket.gethostname().split('.')[0] not in ('sb16','sb16-2'):
        raise SystemExit('Runtime checks require sb16 or sb16-2')
    unittest.main()
