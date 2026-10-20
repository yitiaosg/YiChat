import {copyFile,mkdir} from 'node:fs/promises';
const target=new URL('../public/render/',import.meta.url);await mkdir(target,{recursive:true});
for(const name of ['ycards-render','parachute-scene'])await copyFile(new URL('../../../static/'+name+'.js',import.meta.url),new URL(name+'.js',target));
