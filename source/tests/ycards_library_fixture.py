"""Large editor save fixture; all accounts, points and assets are temporary."""
from ycards_sharing_fixture import *
import base64,io,random
from PIL import Image
rng=random.Random(1234);layers=[]
for index in range(4):
    image=Image.frombytes('RGB',(720,720),rng.randbytes(720*720*3));buf=io.BytesIO();image.save(buf,format='PNG')
    layers.append(dict(id='image'+str(index),type='image',effect='image',position='surface',image='data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()))
large=dict(d,name='大图草稿',layers=layers)
large_draft=store.save('creator',dict(design=large),False)
@app.route('/test-library')
def library_state():
    return dict(draft_id=large_draft['draft_id'],raw_bytes=len(ycards.canonical(large_draft['design']).encode()))
if __name__=='__main__':app.run(port=5079,debug=False,threaded=True)
