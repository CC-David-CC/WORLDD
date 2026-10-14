# InputFocus fixture correction

The SendEvent alias attack must resolve to a foreign application's window.
Its expected result remains native BadAccess.

The original fixture set focus through the supervisor's global core keyboard.
That created a foreign focus only while applications shared that keyboard.
Private application devices correctly leave the attacker's keyboard unfocused.
Native SendEvent returns success without delivery when InputFocus is None.
The original fixture therefore stopped exercising the intended attack.
Both hosts recorded that failure in local/m04-app-input-debug-01/prior.log.

The corrected fixture first queries the attacker's actual ClientPointer.
The trusted supervisor selects that pointer for its own connection.
It then installs the foreign focus through the paired keyboard.
The attacker still sends the original native SendEvent request.
The original BadAccess assertion remains unchanged.
Ordinary application XInput access remains prohibited.

The primary agent reviewed the XLibre XI2 request layouts and authorization path.
David authorized primary-agent review without the local model.
Validate this fixture on both the previous server and the private-device server.
Keep this test correction separate from the production change.
Locked acceptance tests and their lock remain unchanged.
