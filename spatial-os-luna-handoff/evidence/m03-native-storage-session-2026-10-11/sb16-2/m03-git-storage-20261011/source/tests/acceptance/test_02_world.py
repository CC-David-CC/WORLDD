from concurrent.futures import ThreadPoolExecutor
from harness.base import Acceptance,case
from harness.oracles import pose
from harness.rpc import RpcError

class WorldTests(Acceptance):
    @case("P02","world.room_create")
    def test_create_get_list(self):
        r=self.room(); self.assertEqual(r["id"],"room-a"); self.assertEqual(r["revision"],1)
        self.assertEqual(self.get("room-a"),r)
        self.assertIn("room-a",[e["id"] for e in self.call("world.list")["entities"]])
    @case("P02","world.unique_identity")
    def test_duplicate_id_rejected_without_mutation(self):
        self.room(); before=self.call("world.snapshot")
        self.error("ALREADY_EXISTS","world.create",entity={"id":"room-a","kind":"room","label":"duplicate"})
        self.assertEqual(self.call("world.snapshot"),before)
    @case("P02","world.referential_integrity")
    def test_object_cannot_point_at_missing_room(self):
        self.error("NOT_FOUND","world.create",entity={"id":"a","kind":"object","room":"missing","pose":pose()})
    @case("P02","world.pose_update")
    def test_update_advances_revision_once(self):
        self.room(); a=self.obj()
        b=self.call("world.update",id=a["id"],expected_revision=a["revision"],patch={"pose":pose([1,2,3])})
        self.assertEqual(b["revision"],a["revision"]+1); self.assertPose(b["pose"],pose([1,2,3]))
    @case("P02","world.optimistic_concurrency")
    def test_stale_revision_does_not_overwrite(self):
        self.room(); a=self.obj(); self.patch(a["id"],{"label":"winner"})
        self.error("REVISION_CONFLICT","world.update",id=a["id"],expected_revision=a["revision"],patch={"label":"loser"})
        self.assertEqual(self.get(a["id"])["label"],"winner")
    @case("P02","world.concurrent_writers")
    def test_exactly_one_compare_and_swap_wins(self):
        self.room(); a=self.obj()
        def attempt(label):
            try:
                return self.call("world.update",id=a["id"],expected_revision=a["revision"],patch={"label":label})
            except RpcError as e:
                return e.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(attempt,["one","two"]))
        self.assertEqual(sum(isinstance(x,dict) for x in results),1)
        self.assertEqual(results.count("REVISION_CONFLICT"),1)
    @case("P02","world.delete")
    def test_delete_and_missing_get(self):
        self.room(); a=self.obj()
        self.call("world.delete",id=a["id"],expected_revision=a["revision"])
        self.error("NOT_FOUND","world.get",id=a["id"])
    @case("P02","world.room_delete_referenced")
    def test_nonempty_room_delete_rejected(self):
        r=self.room(); self.obj()
        self.error("IN_USE","world.delete",id=r["id"],expected_revision=r["revision"])
    @case("P02","world.immutable_type_identity")
    def test_identity_and_kind_cannot_be_patched(self):
        self.room(); a=self.obj()
        for patch in ({"id":"other"},{"kind":"room"},{"revision":999}):
            self.error("INVALID_ARGUMENT","world.update",id=a["id"],expected_revision=a["revision"],patch=patch)
    @case("P02","world.validation_is_atomic")
    def test_invalid_pose_does_not_partially_update_label(self):
        self.room(); a=self.obj()
        self.error("INVALID_ARGUMENT","world.update",id=a["id"],expected_revision=a["revision"],patch={"label":"bad","pose":pose(q=[0,0,0,0])})
        self.assertEqual(self.get(a["id"]),a)
    @case("P02","world.restart_durability")
    def test_real_process_restart_preserves_objects(self):
        self.room(); self.obj(pose=pose([3,2,-7]))
        before=self.call("world.snapshot")
        self.restart()
        self.assertEqual(self.call("world.snapshot"),before)
    @case("P02","world.crash_durability")
    def test_sigkill_preserves_acknowledged_mutation(self):
        self.room(); self.obj(label="durable-after-ack")
        self.restart(crash=True)
        self.assertEqual(self.get("object-a")["label"],"durable-after-ack")
    @case("P02","world.external_labels_not_shell")
    def test_labels_are_data_including_unicode(self):
        self.room(); text="学校 / лаборатория / $(touch NEVER_EXECUTE)"
        a=self.obj(label=text)
        self.assertEqual(self.get(a["id"])["label"],text)
        self.assertFalse((self.state_dir/"NEVER_EXECUTE").exists())
    @case("P02","world.snapshot_consistency")
    def test_room_move_is_one_reference_change(self):
        self.room(); self.room("room-b"); self.obj()
        self.patch("object-a",{"room":"room-b"})
        entities=self.call("world.list")["entities"]
        matches=[x for x in entities if x["id"]=="object-a"]
        self.assertEqual(len(matches),1); self.assertEqual(matches[0]["room"],"room-b")
