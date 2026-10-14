# Host actor fixture caller note

The shared identity_config() helper verifies admission policy. It does not require observation authority for every admitted actor.

Production host-session tests intentionally remove WORLD_OBSERVE from two admitted fixtures. Those cases verify observation denial while preserving admission. Requiring observation in the shared helper rejects those valid negative fixtures before the runtime test starts.

The standalone A-B-A controller fixture still seeds WORLD_OBSERVE through its normal positive grant profile. The compiled geometry receipt checks observation when the fixture exercises geometry. This note changes no production policy, fixture grant, or native assertion.
