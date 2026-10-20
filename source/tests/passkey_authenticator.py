"""Test-only software authenticator producing real ES256 WebAuthn signatures."""
import hashlib,json,secrets,cbor2
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes
import passkeys as pk

class Authenticator:
 def __init__(self):self.key=ec.generate_private_key(ec.SECP256R1());self.id=secrets.token_bytes(32);self.handle=None;self.count=0
 def create(self,options,rp='yitiao.org',origin='https://yitiao.org',uv=True,cross=False):
  self.handle=options['publicKey']['user']['id'];public=self.key.public_key().public_numbers()
  cose=cbor2.dumps({1:2,3:-7,-1:1,-2:public.x.to_bytes(32,'big'),-3:public.y.to_bytes(32,'big')})
  client=json.dumps(dict(type='webauthn.create',challenge=options['publicKey']['challenge'],origin=origin,crossOrigin=cross)).encode()
  auth=hashlib.sha256(rp.encode()).digest()+bytes([0x45 if uv else 0x41])+b'\x00'*4+b'\x00'*16+len(self.id).to_bytes(2,'big')+self.id+cose
  return dict(id=pk.encode(self.id),rawId=pk.encode(self.id),type='public-key',response=dict(clientDataJSON=pk.encode(client),attestationObject=pk.encode(cbor2.dumps(dict(fmt='none',attStmt={},authData=auth))),transports=['internal']),clientExtensionResults={})
 def get(self,options,rp='yitiao.org',origin='https://yitiao.org',uv=True,cross=False,count=None):
  self.count=self.count+1 if count is None else count
  client=json.dumps(dict(type='webauthn.get',challenge=options['publicKey']['challenge'],origin=origin,crossOrigin=cross)).encode()
  auth=hashlib.sha256(rp.encode()).digest()+bytes([0x05 if uv else 0x01])+self.count.to_bytes(4,'big')
  signature=self.key.sign(auth+hashlib.sha256(client).digest(),ec.ECDSA(hashes.SHA256()))
  return dict(id=pk.encode(self.id),rawId=pk.encode(self.id),type='public-key',response=dict(clientDataJSON=pk.encode(client),authenticatorData=pk.encode(auth),signature=pk.encode(signature),userHandle=self.handle),clientExtensionResults={})
