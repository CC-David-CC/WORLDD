from .base import Acceptance
from .probe import Probe
from .oracles import pose

class XAcceptance(Acceptance):
    xi2=False
    def setUp(self):
        super().setUp();self.room();self.a=self.actor()
        identities=self.call("runtime.identities")
        self.display=identities["display"]
        self.assertTrue(self.display)
    def probe(self,ident="screen",p=(0,0,-4),room="room-a"):
        probe=Probe(self.run_dir,xi2=self.xi2,display=self.display)
        self.addCleanup(probe.close)
        # This reads an independently created X property and maps its real window.
        found=self.until(lambda:self.call("surface.find",probe_id=probe.id),lambda x:x.get("surface") is not None)
        surf=found["surface"]
        self.assertEqual(surf["xid"],probe.xid)
        self.call("surface.place",id=surf["id"],room=room,pose=pose(p),size=[2,2])
        return probe,self.call("surface.get",id=surf["id"])
    def surface(self,id):return self.call("surface.get",id=id)
    def fence(self):
        # Must round-trip through server input queues AND wait for current graphics damage.
        self.call("runtime.fence")
    def aim(self,actor=None,direction=(0,0,-1)):
        return (actor or self.a).act("aim",direction=list(direction))["hit"]
    def click(self,actor=None):
        a=actor or self.a
        a.act("pointer",button=1,pressed=True)
        a.act("pointer",button=1,pressed=False)
        self.fence()
