"""Ordinary actor discovery, durable placement, and genuine native application input."""
import test_three_actor_files as base
import json
import os
from pathlib import Path
import time
import unittest
import core_pb2 as core
import grants_pb2 as grants
import security_pb2 as security
from test_security_identity import NativeClient
from harness.native_application_input import NativeXI2Observer, NativeRawKeyObserver

WORKSPACE=base.WORKSPACE

class ThreeActorApplicationTests(base.ThreeActorFileTests):
    def workspace_grant_roots(self):
        roots=super().workspace_grant_roots()
        if self.read_only_second:
            roots[2].rights.extend([grants.APPLICATION_OBSERVE,grants.APPLICATION_CONTROL,grants.WORKSPACE_PRESENTATION_ATTACH])
        return roots

    def ready_actors(self):
        self.ready_host();producer=self.producer();peers=[self.scripted_peer(0),self.scripted_peer(1)]
        actors=[self.human]+[lambda peer=peer,**action:self.accepted(peer.act(**action)) for peer in peers]
        for actor in actors:actor(workspace_select=core.WorkspaceIdRequest(id=WORKSPACE))
        return producer,peers,actors

    def launch_window(self,actor,index,*,core_events=True):
        output=self.project/('native-'+str(index)+'.jsonl');commands=self.project/('commands-'+str(index));release=self.project/('release-'+str(index));exited=self.project/('exited-'+str(index))
        code=("import subprocess,time\nfrom pathlib import Path\n"
            "out=open('/project/"+output.name+"','w')\n"
            # Confined applications use core events. The authorized observer checks XI2.
            # Explicit XI2 selection remains available for isolation diagnostics.
            "p=subprocess.Popen(['/project/probe',"+("" if core_events else "'--xi2',")+"'--id','m10-app-"+str(index)+"'],stdin=subprocess.PIPE,stdout=out,stderr=out)\n"
            "sent=0\nwhile not Path('/project/"+release.name+"').exists():\n"
            " if p.poll() is not None:\n  Path('/project/"+exited.name+"').write_text(str(p.returncode))\n  raise SystemExit(p.returncode or 1)\n"
            " commands=Path('/project/"+commands.name+"')\n if commands.exists():\n"
            "  data=commands.read_bytes();end=data.rfind(b'\\n')+1\n"
            "  if end>sent:p.stdin.write(data[sent:end]);p.stdin.flush();sent=end\n"
            " time.sleep(.01)\np.terminate();p.wait();out.close()\n")
        launched=actor(process_launch=core.ProcessLaunchRequest(operation_id='native-'+str(index),profile='development',
            argv=['/usr/bin/python3','-c',code])).process
        def rows():
            if not output.exists():return []
            return [json.loads(line) for line in output.read_text().split('\n')[:-1] if line.startswith('{')]
        target=os.environ.get('SPATIAL_SECURITY_EVIDENCE')
        if target:
            def retain():
                if output.exists():
                    path=Path(target);path.mkdir(parents=True,exist_ok=True)
                    (path/(self._testMethodName+'-'+output.name)).write_bytes(output.read_bytes())
            self.addCleanup(retain)
        try:
            self.until(rows,lambda values:any(row.get('event')=='ready' for row in values))
            xi2=next(row for row in rows() if row.get('event')=='ready').get('xi2')
            self.assertEqual(xi2,not core_events,'Native app probe selected the wrong input protocol')
        except AssertionError:
            diagnostic=self.execution/'applications'/launched.process_id/'setup.log'
            self.fail('Native application did not become ready. Output: '+(output.read_text(errors='replace') if output.exists() else '<missing>')+
                '\nSandbox setup: '+(diagnostic.read_text(errors='replace') if diagnostic.exists() else '<missing>'))
        return launched.process_id,rows,commands

    def native_observer(self,rows,*,raw_keys=False):
        ready=next(row for row in rows() if row.get('event')=='ready')
        client=NativeClient(self.display,self.cookie)
        try:
            identity=client.inspect()
            self.assertEqual(identity.role,security.SUPERVISOR)
            self.assertEqual(identity.server_id,self.supervisor.inspect().server_id)
            observer=(NativeRawKeyObserver if raw_keys else NativeXI2Observer)(client,ready['xid'])
        except BaseException:
            client.close();raise
        self.addCleanup(observer.close)
        target=os.environ.get('SPATIAL_SECURITY_EVIDENCE')
        if target:
            def retain():
                p=Path(target);p.mkdir(parents=True,exist_ok=True)
                (p/(self._testMethodName+'-native-xi2-'+str(ready['xid'])+'.json')).write_text(
                    json.dumps({'window':ready['xid'],'events':observer.events},indent=2)+'\n')
            self.addCleanup(retain)
        return observer

    def discover(self,actor,application):
        return actor(application_surfaces=core.ActorApplicationSurfacesRequest(protocol_version=1,application_id=application)).application_surfaces

    def mapped(self,actor,application):
        return self.until(lambda:self.discover(actor,application),lambda value:len(value.surfaces)>0).surfaces[0]

    def placement_request(self,actor,receipt,x=0):
        revision=actor(observe=core.ActorObserveRequest(width=128,height=128)).observation.world_revision
        return core.ActorApplicationPlaceRequest(protocol_version=1,operation_id=os.urandom(16),discovery_id=receipt.discovery_id,
            transform=core.Pose(position=core.Vec3(x=x,z=-2),orientation=core.Quaternion(w=1)),
            size=core.Vec2(x=.7,y=.6),expected_world_revision=revision)

    def test_exact_placement_retry_returns_receipt_after_native_application_exits(self):
        producer,peers,actors=self.ready_actors()
        application,rows,commands=self.launch_window(actors[1],1)
        receipt=self.mapped(actors[1],application)
        request=self.placement_request(actors[1],receipt)
        original=actors[1](application_place=request).application_placement
        self.assertTrue(original.slot_id)
        exited=self.project/'exited-1'
        commands.write_text('quit 1\n')
        self.until(exited.exists,lambda value:value)
        self.assertEqual(exited.read_text(),'0')
        # A distinct actor refreshes native inventory so the owner keeps its
        # original discovery receipt for the lost-reply retry.
        absent=self.until(lambda:self.discover(actors[2],application),lambda value:not value.surfaces)
        self.assertEqual(len(absent.surfaces),0)
        retried=actors[1](application_place=request).application_placement
        self.assertEqual(retried.SerializeToString(),original.SerializeToString())
        import sqlite3
        with sqlite3.connect('file:'+str(self.root/'world/world.db')+'?mode=ro',uri=True) as database:
            self.assertEqual(database.execute('SELECT COUNT(*) FROM entities WHERE kind=?',(core.SURFACE_SLOT,)).fetchone()[0],1)
            self.assertEqual(database.execute('SELECT COUNT(*) FROM workspace_presentation_ops WHERE kind=4').fetchone()[0],1)
        for actor in actors:actor(stop=core.ActorStopRequest())
        for peer in peers:peer.close()
        self.shutdown_checked()

    def visible(self,actor,handle):
        observation=self.until(lambda:actor(observe=core.ActorObserveRequest(width=256,height=192,include_pixels=True)).observation,
            lambda value:any(item.id==handle.id and item.generation==handle.generation and item.visible_pixels for item in value.visible))
        self.assertEqual(len(observation.image.rgb8),256*192*3)
        return next(item for item in observation.visible if item.id==handle.id)

    def focus(self,actor,handle):
        item=self.visible(actor,handle);aim=actor(aim=core.ActorAimRequest(direction=item.direction)).aim
        self.assertEqual(aim.hit.id,handle.id);self.assertEqual(aim.hit.generation,handle.generation)
        actor(pointer=core.ActorPointerRequest(button=1,pressed=True))
        actor(pointer=core.ActorPointerRequest(button=1,pressed=False))

    def tap(self,actor,keysym,modifiers=()):
        chord=core.ActorKeyChord(keysyms=[*modifiers,keysym])
        actor(key_tap_batch=core.ActorKeyTapBatchRequest(chords=[chord]))

    def test_human_and_two_agents_place_real_windows_and_use_distinct_native_devices(self):
        producer,peers,actors=self.ready_actors();applications=[];devices=[]
        for index,actor in enumerate(actors):
            application,rows,_=self.launch_window(actor,index);receipt=self.mapped(actor,application)
            self.assertEqual(len(receipt.discovery_id),32)
            placed=actor(application_place=self.placement_request(actor,receipt,x=(index-1)*.9)).application_placement
            self.assertTrue(placed.slot_id);self.assertEqual(placed.surface,receipt.surface);self.assertEqual(placed.slot_revision,1)
            self.focus(actor,placed.surface)
            key=chr(ord('a')+index)
            self.tap(actor,key)
            witnessed=self.until(rows,lambda values:all(any(r.get('event')=='key' and r.get('keysym')==ord(key) and r.get('pressed')==pressed for r in values) for pressed in [True,False]))
            keycodes={r['keycode'] for r in witnessed if r.get('event')=='key' and r.get('keysym')==ord(key)}
            self.assertEqual(len(keycodes),1);code=next(iter(keycodes))
            observer=self.native_observer(rows);binding=observer.controlled_devices()
            # XI2-first delivery has a separate fresh action phase. The app's
            # core receipt above supplies its independently measured keycode.
            self.tap(actor,key)
            observed=observer.read_until(lambda values:all(any(r['kind']=='key' and r['detail']==code and r['pressed']==pressed for r in values) for pressed in [True,False]),3)
            delivered=[r for r in observed if r['kind']=='key' and r['detail']==code]
            self.assertEqual([r['pressed'] for r in delivered],[True,False])
            for event in delivered:
                self.assertEqual(event['device'],binding['keyboard'])
                self.assertEqual(binding['devices'].get(event['source']),(4,binding['keyboard']))
                self.assertIn(event['source'],binding['enabled_devices'])
            observer.deselect()
            devices.append((binding['pointer'],binding['keyboard']))
            applications.append((application,placed.surface,rows,observer))
        self.assertEqual(len({pair[0] for pair in devices}),3)
        self.assertEqual(len({pair[1] for pair in devices}),3)
        first_application,first_surface,first_rows,first_observer=applications[0]
        self.focus(actors[0],first_surface);actors[0](key=core.ActorKeyRequest(keysym='Shift_L',pressed=True))
        before=len(first_rows());item=self.visible(actors[1],first_surface)
        actors[1](aim=core.ActorAimRequest(direction=item.direction))
        denied=peers[0].act(pointer=core.ActorPointerRequest(button=1,pressed=True))
        self.assertEqual(denied.error.code,core.BUSY,denied);self.assertTrue(denied.cancellation_confirmed)
        self.assertTrue(denied.context.available)
        self.assertEqual(first_observer.controlled_devices()['pointer'],devices[0][0])
        actors[0](stop=core.ActorStopRequest())
        self.until(first_rows,lambda values:any(row.get('event')=='key' and row.get('keysym')==65505 and not row.get('pressed') for row in values[before:]))
        self.focus(actors[1],first_surface)
        moved=first_observer.controlled_devices()
        self.assertEqual((moved['pointer'],moved['keyboard']),devices[1])
        self.tap(actors[1],'q')
        self.until(first_rows,lambda values:any(row.get('event')=='key' and row.get('pressed') and row.get('text_hex')=='71' for row in values))
        for actor in actors:actor(stop=core.ActorStopRequest())
        for peer in peers:peer.close()
        self.shutdown_checked()

    def test_agent_key_tap_batches_preserve_every_edge_at_high_rate(self):
        producer,peers,actors=self.ready_actors()
        application,rows,_=self.launch_window(actors[1],1)
        receipt=self.mapped(actors[1],application)
        placed=actors[1](application_place=self.placement_request(actors[1],receipt)).application_placement
        self.focus(actors[1],placed.surface)
        text='abcdefghijklmnopqrstuvwxyz'*12+'abcdefghijklmnopqrst'
        self.assertEqual(len(text),332)
        app_start=len([row for row in rows() if row.get('event')=='key'])
        def submit():
            started=time.monotonic()
            for offset in range(0,len(text),32):
                chords=[core.ActorKeyChord(keysyms=[key]) for key in text[offset:offset+32]]
                actors[1](key_tap_batch=core.ActorKeyTapBatchRequest(chords=chords))
            return time.monotonic()-started
        # XI2 selection suppresses simultaneous core delivery. Test each path
        # with a full independent batch and require the rate for both batches.
        app_elapsed=submit()
        app_rows=self.until(rows,lambda values:len([row for row in values if row.get('event')=='key'])>=app_start+2*len(text))
        key_rows=[row for row in app_rows if row.get('event')=='key'][app_start:]
        self.assertEqual(len(key_rows),2*len(text),key_rows[-12:])
        self.assertEqual([(row['keysym'],row['pressed']) for row in key_rows],
            [(ord(key),pressed) for key in text for pressed in (True,False)])
        codes={chr(row['keysym']):row['keycode'] for row in key_rows if row['pressed']}
        self.assertEqual(set(codes),set(text))
        observer=self.native_observer(rows)
        binding=observer.controlled_devices();native_start=len(observer.events)
        native_elapsed=submit()
        measurement=dict(taps_per_phase=len(text),actor_actions_per_phase=(len(text)+31)//32,
            application_submission_seconds=app_elapsed,native_submission_seconds=native_elapsed,
            application_taps_per_second=len(text)/max(app_elapsed,1e-9),
            native_taps_per_second=len(text)/max(native_elapsed,1e-9))
        evidence=os.environ.get('SPATIAL_SECURITY_EVIDENCE')
        if evidence:
            path=Path(evidence);path.mkdir(parents=True,exist_ok=True)
            record=path/(self._testMethodName+'-key-rate.json')
            record.write_text(json.dumps(measurement,sort_keys=True,indent=2)+'\n');record.chmod(0o600)
        print('M10 bounded key-tap rate:',json.dumps(measurement,sort_keys=True))
        witnessed=observer.read_until(lambda events:len([event for event in events[native_start:]
            if event['kind']=='key'])>=2*len(text),timeout=20)
        delivered=[event for event in witnessed[native_start:] if event['kind']=='key']
        self.assertEqual(len(delivered),2*len(text),delivered[-12:])
        self.assertTrue(all(event['device']==binding['keyboard'] and
            binding['devices'].get(event['source'])==(4,binding['keyboard']) and
            event['source'] in binding['enabled_devices'] for event in delivered),delivered[:8])
        expected=[(codes[key],pressed) for key in text for pressed in (True,False)]
        self.assertEqual([(event['detail'],event['pressed']) for event in delivered],expected)
        self.assertGreaterEqual(measurement['application_taps_per_second'],100,'Application phase did not sustain 100 taps per second')
        self.assertGreaterEqual(measurement['native_taps_per_second'],100,'Native observer phase did not sustain 100 taps per second')
        for actor in actors:actor(stop=core.ActorStopRequest())
        for peer in peers:peer.close()
        self.shutdown_checked()

    def test_atomic_key_tap_survives_delayed_client_completion_without_repeat(self):
        producer,peers,actors=self.ready_actors()
        application,rows,_=self.launch_window(actors[1],1)
        receipt=self.mapped(actors[1],application)
        placed=actors[1](application_place=self.placement_request(actors[1],receipt)).application_placement
        self.focus(actors[1],placed.surface)
        observer=self.native_observer(rows,raw_keys=True)
        binding=observer.controlled_devices();native_start=len(observer.events)
        app_start=len([row for row in rows() if row.get('event')=='key'])

        started=time.monotonic()
        reply=peers[0].act(delay_client_completion_seconds=.85,
            key_tap_batch=core.ActorKeyTapBatchRequest(chords=[core.ActorKeyChord(keysyms=['a'])]))
        elapsed=time.monotonic()-started
        self.assertTrue(reply.HasField('action'),reply)
        self.assertTrue(reply.action.accepted,reply)
        self.assertGreaterEqual(elapsed,.75,'The actor call did not stay pending past the XKB repeat delay')

        app_rows=self.until(rows,lambda values:len([row for row in values if row.get('event')=='key' and
            row.get('keysym')==ord('a')])>=2)
        key_rows=[row for row in app_rows if row.get('event')=='key' and row.get('keysym')==ord('a')]
        self.assertEqual(len(key_rows),2,key_rows)
        self.assertEqual([row['pressed'] for row in key_rows],[True,False])
        code=key_rows[0]['keycode']

        events=observer.read_until(lambda values:len([event for event in values[native_start:]
            if event['kind']=='key' and event['detail']==code])>=2,3)
        time.sleep(.75)
        observer.drain(.25)
        delivered=[event for event in observer.events[native_start:]
            if event['kind']=='key' and event['detail']==code]
        self.assertEqual([event['pressed'] for event in delivered],[True,False],delivered)
        self.assertTrue(all(event['device']==binding['keyboard'] and
            binding['devices'].get(event['source'])==(4,binding['keyboard']) and
            event['source'] in binding['enabled_devices'] for event in delivered),delivered)
        self.assertEqual(len([row for row in rows() if row.get('event')=='key' and
            row.get('keysym')==ord('a')]),2)
        self.assertTrue(events)
        for actor in actors:actor(stop=core.ActorStopRequest())
        for peer in peers:peer.close()
        self.shutdown_checked()

    def test_explicit_key_hold_survives_long_turn_and_stop_releases_it(self):
        producer,peers,actors=self.ready_actors()
        application,rows,_=self.launch_window(actors[1],1)
        receipt=self.mapped(actors[1],application)
        placed=actors[1](application_place=self.placement_request(actors[1],receipt)).application_placement
        self.focus(actors[1],placed.surface)
        observer=self.native_observer(rows,raw_keys=True)
        binding=observer.controlled_devices();native_start=len(observer.events)

        pressed=peers[0].act(key=core.ActorKeyRequest(keysym='a',pressed=True))
        self.assertTrue(pressed.action.accepted,pressed)
        self.until(rows,lambda values:any(row.get('event')=='key' and row.get('keysym')==ord('a') and
            row.get('pressed') for row in values))
        time.sleep(.85)
        state=peers[0].call(inspect=True).state
        self.assertIn('a',state.held_keys)

        before_stop=len(observer.events)
        stopped=peers[0].act(stop=core.ActorStopRequest())
        self.assertTrue(stopped.action.accepted,stopped)
        self.assertEqual(list(peers[0].call(inspect=True).state.held_keys),[])
        key_rows=self.until(rows,lambda values:any(row.get('event')=='key' and row.get('keysym')==ord('a') and
            not row.get('pressed') for row in values))
        code=next(row['keycode'] for row in key_rows if row.get('event')=='key' and row.get('keysym')==ord('a'))
        observer.read_until(lambda values:any(event['kind']=='key' and event['detail']==code and
            not event['pressed'] for event in values[before_stop:]),3)
        time.sleep(.1)
        observer.drain(.25)
        released=[event for event in observer.events[before_stop:]
            if event['kind']=='key' and event['detail']==code]
        self.assertTrue(released and not released[-1]['pressed'],released)
        self.assertTrue(all(event['device']==binding['keyboard'] and
            binding['devices'].get(event['source'])==(4,binding['keyboard']) and
            event['source'] in binding['enabled_devices'] for event in released),released)
        for peer in peers:peer.close()
        self.shutdown_checked()

    def test_another_actor_cannot_use_a_discovery_receipt_and_resize_rejects_old_generation(self):
        producer,peers,actors=self.ready_actors();application,rows,commands=self.launch_window(actors[0],0)
        receipt=self.mapped(actors[1],application);request=self.placement_request(actors[1],receipt)
        denied=peers[1].act(application_place=request)
        self.assertEqual(denied.error.code,core.FORBIDDEN,denied);self.assertFalse(denied.context.available)
        commands.write_text('resize 1 220 140\n')
        self.until(rows,lambda values:any(row.get('event')=='ack' and row.get('seq')==1 for row in values))
        newer=self.until(lambda:self.discover(actors[0],application),lambda value:value.surfaces and value.surfaces[0].surface.generation!=receipt.surface.generation)
        self.assertEqual(newer.surfaces[0].surface.id,receipt.surface.id)
        denied=peers[0].act(application_place=request)
        self.assertEqual(denied.error.code,core.STALE_TARGET,denied);self.assertFalse(denied.context.available)
        import sqlite3
        with sqlite3.connect('file:'+str(self.root/'world/world.db')+'?mode=ro',uri=True) as database:
            self.assertEqual(database.execute('SELECT COUNT(*) FROM entities WHERE kind=?',(core.SURFACE_SLOT,)).fetchone()[0],0)
            self.assertEqual(database.execute('SELECT COUNT(*) FROM workspace_presentation_ops WHERE kind=4').fetchone()[0],0)
        for peer in peers:peer.close()
        self.shutdown_checked()

    def test_read_only_actor_can_observe_and_place_but_cannot_control_a_writable_application(self):
        self.read_only_second=True;producer,peers,actors=self.ready_actors()
        application,rows,_=self.launch_window(actors[0],0);receipt=self.mapped(actors[2],application)
        placed=actors[2](application_place=self.placement_request(actors[2],receipt)).application_placement
        item=self.visible(actors[2],placed.surface);actors[2](aim=core.ActorAimRequest(direction=item.direction))
        before=[row for row in rows() if row.get('event') in ['button','key']]
        denied=peers[1].act(pointer=core.ActorPointerRequest(button=1,pressed=True))
        self.assertEqual(denied.error.code,core.FORBIDDEN,denied);self.assertTrue(denied.cancellation_confirmed)
        self.assertTrue(denied.context.available)
        self.assertEqual([row for row in rows() if row.get('event') in ['button','key']],before)
        self.focus(actors[0],placed.surface)
        self.tap(actors[0],'d')
        self.until(rows,lambda values:any(row.get('event')=='key' and row.get('pressed') and row.get('keysym')==ord('d') for row in values))
        for actor in actors:actor(stop=core.ActorStopRequest())
        for peer in peers:peer.close()
        self.shutdown_checked()

    def test_interrupted_human_stop_preserves_world_application_and_other_actors(self):
        import concurrent.futures
        import signal
        producer,peers,actors=self.ready_actors()
        application,rows,_=self.launch_window(actors[0],0)
        receipt=self.mapped(actors[0],application)
        placed=actors[0](application_place=self.placement_request(actors[0],receipt)).application_placement
        self.focus(actors[0],placed.surface)
        actors[0](key=core.ActorKeyRequest(keysym='Shift_L',pressed=True))
        self.until(rows,lambda values:any(r.get('event')=='key' and r.get('keysym')==65505 and r.get('pressed') for r in values))
        retained=self.retained_pids();world_pid=next(iter(retained.values()))[0]
        pids=[self.host_process.pid,self.server.pid,world_pid]+self.live_compositor_pids()
        starts={pid:self.process_start(pid) for pid in pids}
        self.until(self.foreground_pixels,lambda pixels:any(pixels[0::4]) or any(pixels[1::4]) or any(pixels[2::4]))
        log_offset=len(self.host_log.read_text());cleanup_serial=None
        pinned=os.pidfd_open(world_pid);pool=concurrent.futures.ThreadPoolExecutor(max_workers=1)
        pending=None;finished_while_paused=False;started=time.monotonic()
        try:
            signal.pidfd_send_signal(pinned,signal.SIGSTOP)
            self.until(lambda:next(line.split()[1] for line in Path('/proc/'+str(world_pid)+'/status').read_text().splitlines() if line.startswith('State:')),
                lambda value:value in ('T','t'))
            started=time.monotonic()
            pending=pool.submit(self.shared,'propose',value=core.ActorActionRequest(stop=core.ActorStopRequest()))
            try:
                reply=pending.result(timeout=2)
                reply_elapsed=time.monotonic()-started
                finished_while_paused=True
                deadline=started+2
                while True:
                    pixels=self.foreground_pixels()
                    if not any(pixels[0::4]) and not any(pixels[1::4]) and not any(pixels[2::4]):break
                    self.assertLess(time.monotonic(),deadline,'Foreground pixels remained visible while exact world was paused')
                    time.sleep(.01)
                self.assertLess(time.monotonic(),deadline,'Native clearing exceeded the paused-world bound')
                import re
                paused_log=self.host_log.read_text()[log_offset:]
                queued=re.findall(r'Local actor retirement queued serial=(\d+)',paused_log)
                self.assertEqual(len(queued),1,paused_log);cleanup_serial=queued[0]
                self.until(lambda:self.host_log.read_text()[log_offset:],
                    lambda log:'Local actor retirement native-cleared serial='+cleanup_serial+'\n' in log)
                self.assertNotIn('Local actor retirement complete serial='+cleanup_serial+'\n',self.host_log.read_text()[log_offset:])
                self.assertIsNone(self.host_process.poll())
                geometry=self.shared('geometry_status');self.assertTrue(geometry.accepted,geometry)
                self.assertFalse(geometry.geometry_status.available,geometry)
            except concurrent.futures.TimeoutError:
                pass
        finally:
            signal.pidfd_send_signal(pinned,signal.SIGCONT);os.close(pinned)
            pool.shutdown(wait=True)
        self.assertTrue(finished_while_paused,'Host did not return the interrupted request while the exact world was paused')
        self.assertGreaterEqual(reply_elapsed,.3);self.assertLess(reply_elapsed,2)
        self.assertFalse(reply.accepted,reply);self.assertFalse(reply.HasField('proposed'),reply)
        self.assertIn('interrupt',reply.detail.lower())
        self.assertIsNone(self.host_process.poll())
        status=self.shared('controls_status');self.assertTrue(status.accepted,status)
        self.assertFalse(status.controls.available)
        self.assertFalse(status.controls.capture_active)
        self.assertTrue(status.controls.capture_retired)
        self.assertEqual(status.controls.capture_source_slot,0)
        self.assertEqual(self.retained_pids(),retained)
        self.assertEqual({pid:self.process_start(pid) for pid in pids},starts)
        self.until(lambda:self.host_log.read_text()[log_offset:],
            lambda log:'Local actor retirement complete serial='+cleanup_serial+'\n' in log)
        self.until(rows,lambda values:any(r.get('event')=='key' and r.get('keysym')==65505 and not r.get('pressed') for r in values))
        recovered=self.mapped(actors[1],application);self.assertEqual(recovered.surface,placed.surface)
        self.focus(actors[1],placed.surface)
        self.tap(actors[1],'v')
        self.until(rows,lambda values:any(r.get('event')=='key' and r.get('pressed') and r.get('text_hex')=='76' for r in values))
        for actor in actors[1:]:actor(stop=core.ActorStopRequest())
        for peer in peers:peer.close()
        self.shutdown_checked()

    def test_missing_program_reports_cause_and_other_actors_keep_moving(self):
        producer,peers,actors=self.ready_actors()
        name='missing-program-'+os.urandom(12).hex()
        self.assertFalse((self.project/name).exists())
        before=self.human(observe=core.ActorObserveRequest(width=128,height=128)).observation
        denied=peers[1].act(process_launch=core.ProcessLaunchRequest(operation_id='missing-program',
            profile='development',argv=['/project/'+name]))
        self.assertTrue(denied.HasField('error'),denied);self.assertFalse(denied.HasField('action'))
        self.assertIn('Application exec failed',denied.error.message)
        # An uncertain mutating outcome fences only its own actor transport.
        self.assertFalse(denied.context.available)
        initial=[self.state(),peers[0].call(inspect=True).state]
        self.wait_processed(producer.key('w'))
        motion=peers[0].motion([.8,0,0,0,0,0])
        self.until(lambda:self.state(),lambda state:state.pose.position.z<initial[0].pose.position.z-.05)
        self.wait_processed(producer.key('w',False));self.stop();motion.close()
        self.accepted(peers[0].act(stop=core.ActorStopRequest()))
        final=[self.state(),peers[0].call(inspect=True).state]
        for old,new in zip(initial,final):
            self.assertNotEqual(old.pose,new.pose);self.assertEqual(list(new.axes),[0]*6)
        after=self.human(observe=core.ActorObserveRequest(width=128,height=128)).observation
        self.assertEqual(before.world_revision,after.world_revision)
        for peer in peers:peer.close()
        self.shutdown_checked()
        self.assertIn('No such file or directory',denied.error.message)


if __name__=='__main__':
    names=[name for name in ThreeActorApplicationTests.__dict__ if name.startswith('test_')]
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(ThreeActorApplicationTests(name) for name in names)).wasSuccessful())
