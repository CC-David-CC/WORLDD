"""Public font replies authorize their names without exposing private atoms."""
import struct
import unittest
import test_atom_privacy as atom


class FontAtomVisibilityTests(atom.AtomPrivacyTests):
    def open_font(self,client,name=b'fixed'):
        font=client.xid()
        self.assertIsNone(client.operation(45,client.pack('IH2x',font,len(name))+name))
        return font

    def font_properties(self,client,font):
        reply=client.operation(47,client.pack('I',font),reply=True)
        self.assertIsInstance(reply,tuple)
        head,body=reply;data=head+body
        self.assertGreaterEqual(len(data),60)
        count=struct.unpack_from(client.endian+'H',data,46)[0]
        self.assertGreater(count,0);self.assertLessEqual(60+count*8,len(data))
        return [struct.unpack_from(client.endian+'II',data,60+8*i) for i in range(count)]

    def checked_properties(self,client,properties):
        names={}
        for name,value in properties:
            text=self.name(client,name);self.assertIsInstance(text,bytes)
            names[text]=value
        self.assertIn(b'FONT',names)
        font_name=self.name(client,names[b'FONT']);self.assertIsInstance(font_name,bytes)
        self.assertIn(b'-',font_name)
        return names

    def query_font(self,endian):
        self.scene(endian=endian);client=self.other
        private_name=self.secret();private=self.intern(self.owner,private_name)
        self.assertEqual(self.name(client,private),5)
        properties=self.font_properties(client,self.open_font(client))
        self.checked_properties(client,properties)
        self.assertEqual(self.name(client,private),5)
        self.assertEqual(self.intern(client,private_name,only_existing=True),0)

    def test_query_font_names_are_readable_without_private_atom_disclosure(self):
        self.query_font('<')

    def test_swapped_query_font_names_are_readable_without_private_atom_disclosure(self):
        self.query_font('>')

    def list_font(self,endian):
        self.scene(endian=endian);client=self.other
        private=self.intern(self.owner,self.secret())
        client.send(50,client.pack('HH',1,5)+b'fixed')
        records=[]
        while True:
            head=client.exact(32)
            self.assertEqual(head[0],1);self.assertEqual(struct.unpack_from(endian+'H',head,2)[0],client.sequence%65536)
            size=struct.unpack_from(endian+'I',head,4)[0]*4;self.assertLessEqual(size,65536)
            data=head+client.exact(size)
            if not head[1]:break
            self.assertGreaterEqual(len(data),60);count=struct.unpack_from(endian+'H',data,46)[0]
            self.assertLessEqual(60+8*count+head[1],len(data))
            records.append([struct.unpack_from(endian+'II',data,60+8*i) for i in range(count)])
        self.assertEqual(len(records),1);self.checked_properties(client,records[0])
        self.assertEqual(self.name(client,private),5)

    def test_list_fonts_with_info_names_are_readable_without_private_atom_disclosure(self):
        self.list_font('<')

    def test_swapped_list_fonts_with_info_names_are_readable_without_private_atom_disclosure(self):
        self.list_font('>')

    def test_denied_foreign_font_query_does_not_authorize_private_atoms(self):
        self.scene();private=self.intern(self.owner,self.secret());font=self.open_font(self.owner)
        self.assertEqual(self.other.operation(47,self.other.pack('I',font),reply=True),10)
        self.assertEqual(self.name(self.other,private),5)


    def test_numeric_font_value_does_not_declare_a_private_atom(self):
        root=self.scene();font_name=b'10x20'
        public=self.font_properties(root,self.open_font(root,font_name))
        named={self.name(root,name):value for name,value in public}
        numeric=named[b'POINT_SIZE'];self.assertGreater(numeric,68)
        # A numeric identifier can already name an unrelated dynamic atom.
        # Declare that name only for the first application. The second must
        # not acquire it by receiving the same integer as a font point size.
        unrelated=self.name(root,numeric);self.assertIsInstance(unrelated,bytes)
        self.assertNotIn(numeric,{name for name,_ in public})
        secret=self.intern(self.owner,unrelated)
        self.assertEqual(secret,numeric);self.assertEqual(self.name(self.other,secret),5)
        properties=self.font_properties(self.other,self.open_font(self.other,font_name))
        self.checked_properties(self.other,properties)
        self.assertEqual(self.name(self.other,secret),5)

    def test_font_atom_budget_failure_publishes_no_partial_declarations(self):
        root=self.scene();client=self.other
        public=self.font_properties(root,self.open_font(root))
        font_value=next(value for name,value in public if name==18) # Predefined XA_FONT.
        self.assertEqual(self.name(client,font_value),5)
        prefix=self.secret()
        for index in range(65535):
            self.intern(client,prefix+b'-'+str(index).encode())
        font=self.open_font(client)
        self.assertEqual(client.operation(47,client.pack('I',font),reply=True),11)
        self.assertEqual(self.name(client,font_value),5)
        # The failed font reply did not consume even the last available entry.
        self.assertGreater(self.intern(client,prefix+b'-last'),0)
        name=prefix+b'-overflow'
        self.assertEqual(client.operation(16,client.pack('H2x',len(name))+name,reply=True),11)


if __name__=='__main__':
    names=[name for name in FontAtomVisibilityTests.__dict__ if name.startswith('test_')]
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(
        unittest.TestSuite(FontAtomVisibilityTests(name) for name in names)).wasSuccessful())
