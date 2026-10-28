"""Real editors and terminals use ordinary actor actions and real workspace files."""
import test_three_actor_applications as application
import json
import os
import re
import struct
from pathlib import Path
import unittest
import core_pb2 as core
import file_actions_pb2 as files


class ThreeActorEditorTerminalTests(application.ThreeActorApplicationTests):
    def launch_program(self,actor,index,argv):
        log=self.project/('program-'+str(index)+'.log')
        exited=self.project/('program-'+str(index)+'.exit')
        code=("import subprocess\nfrom pathlib import Path\n"
            "with open('/project/"+log.name+"','w') as log:\n"
            " p=subprocess.Popen("+repr(argv)+",stdout=log,stderr=log)\n"
            " result=p.wait()\n"
            "Path('/project/"+exited.name+"').write_text(str(result))\n"
            "raise SystemExit(result)\n")
        launched=actor(process_launch=core.ProcessLaunchRequest(operation_id='program-'+str(index),
            profile='development',argv=['/usr/bin/python3','-c',code])).process
        target=os.environ.get('SPATIAL_SECURITY_EVIDENCE')
        if target:
            def retain():
                directory=Path(target);directory.mkdir(parents=True,exist_ok=True)
                for p in [log,exited]:
                    if p.exists():(directory/(self._testMethodName+'-'+p.name)).write_bytes(p.read_bytes())
            self.addCleanup(retain)
        try:
            receipt=self.mapped(actor,launched.process_id)
        except AssertionError:
            output=log.read_text(errors='replace') if log.exists() else '<missing>'
            atom=re.search(r'Atom id in failed request:\s*(0x[0-9a-fA-F]+)',output)
            detail=''
            if atom:
                head,data=self.supervisor.request(17,struct.pack('<I',int(atom.group(1),16)))
                length=struct.unpack_from('<H',head,8)[0]
                detail='\nTrusted diagnostic atom name: '+repr(data[:length])
            self.fail('Application did not map. Exit: '+(exited.read_text() if exited.exists() else '<running>')+
                '\nOutput: '+output+detail)
        return launched,receipt

    def place_program(self,actor,receipt,index):
        request=self.placement_request(actor,receipt,x=(index-1)*.8)
        request.transform.position.y=.65;request.transform.position.z=-1.4
        request.size.x=.65;request.size.y=.8
        return actor(application_place=request).application_placement

    def click_program(self,actor,placed,u=.5,v=.7):
        self.visible(actor,placed.surface)
        observation=actor(observe=core.ActorObserveRequest(width=128,height=128)).observation
        q=observation.pose.orientation
        self.assertEqual((q.x,q.y,q.z,q.w),(0,0,0,1),'This layout fixture requires its unchanged entry camera')
        position=observation.pose.position;panel=placed.transform.position
        direction=core.Vec3(x=panel.x+placed.size.x*(u-.5)-position.x,
            y=panel.y+placed.size.y*(.5-v)-position.y,z=panel.z-position.z)
        aim=actor(aim=core.ActorAimRequest(direction=direction)).aim
        self.assertEqual(aim.hit.id,placed.surface.id);self.assertEqual(aim.hit.generation,placed.surface.generation)
        actor(pointer=core.ActorPointerRequest(button=1,pressed=True))
        actor(pointer=core.ActorPointerRequest(button=1,pressed=False))

    def tap(self,actor,keysym,modifiers=()):
        chord=core.ActorKeyChord(keysyms=[*modifiers,keysym])
        actor(key_tap_batch=core.ActorKeyTapBatchRequest(chords=[chord]))

    def type_text(self,actor,text):
        symbols={' ':'space','\n':'Return','-':'minus','/':'slash','.':'period'}
        for character in text:
            if character=='>':
                self.tap(actor,'period',('Shift_L',))
            else:
                self.assertTrue(character in symbols or character.isascii() and character.isalnum())
                self.tap(actor,symbols.get(character,character))

    def cabinet_content(self,actor,target,name):
        directory=self.cabinet(actor,target,browse=files.FileBrowse(page_size=64)).directory
        entry=next(item for item in directory.entries if item.name==name.encode())
        return self.cabinet(actor,target,read=files.FileRead(file_handle=entry.handle,maximum_bytes=256)).content.data

    def finish_programs(self,peers,actors):
        for actor in actors:actor(stop=core.ActorStopRequest())
        for peer in peers:peer.close()
        self.shutdown_checked()

    def test_human_and_two_agents_type_and_save_real_editor_files(self):
        previous_trace=os.environ.get('SPATIAL_TRACE_STOP')
        os.environ['SPATIAL_TRACE_STOP']='1'
        def restore_stop_trace():
            if previous_trace is None:os.environ.pop('SPATIAL_TRACE_STOP',None)
            else:os.environ['SPATIAL_TRACE_STOP']=previous_trace
        self.addCleanup(restore_stop_trace)
        producer,peers,actors,target=self.prepare_cabinet()
        for index,actor in enumerate(actors):
            name='editor-'+str(index)+'.txt'
            directory=self.cabinet(actor,target,browse=files.FileBrowse(page_size=64)).directory
            self.cabinet(actor,target,create=files.FileCreate(directory_handle=directory.handle,basename=name.encode(),content=b''))
            launched,receipt=self.launch_program(actor,index,['/usr/bin/xedit','-geometry','600x320',
                '-xrm','*baseTranslations: #override <Ctrl>X,<Ctrl>S:save-file()','/project/'+name])
            placed=self.place_program(actor,receipt,index);self.click_program(actor,placed)
            content=('edited by actor '+str(index)+'\n').encode()
            self.type_text(actor,content.decode())
            self.tap(actor,'x',('Control_L',));self.tap(actor,'s',('Control_L',))
            self.until(lambda:(self.project/name).read_bytes(),lambda value:value==content)
            self.assertEqual(self.cabinet_content(actor,target,name),content)
        self.finish_programs(peers,actors)

    def test_human_and_two_agents_type_commands_in_real_terminals(self):
        producer,peers,actors,target=self.prepare_cabinet()
        for index,actor in enumerate(actors):
            name='terminal-'+str(index)+'.txt';self.assertFalse((self.project/name).exists())
            launched,receipt=self.launch_program(actor,index,['/usr/bin/xterm','-ut','-fn','fixed',
                '-geometry','60x12','-e','/bin/sh','-i'])
            placed=self.place_program(actor,receipt,index);self.click_program(actor,placed)
            self.type_text(actor,'echo terminal-actor-'+str(index)+' > /project/'+name+'\n')
            content=('terminal-actor-'+str(index)+'\n').encode()
            self.until(lambda:(self.project/name).read_bytes() if (self.project/name).exists() else None,
                lambda value:value==content)
            self.assertEqual(self.cabinet_content(actor,target,name),content)
        self.finish_programs(peers,actors)


if __name__=='__main__':
    names=[name for name in ThreeActorEditorTerminalTests.__dict__ if name.startswith('test_')]
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(
        unittest.TestSuite(ThreeActorEditorTerminalTests(name) for name in names)).wasSuccessful())
