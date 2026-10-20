#!/usr/bin/env python3
"""Generate ONE private development signing identity, never overwrite it.
Run only when building the Android App in the next session. No key is bundled.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True,help='Private directory OUTSIDE source checkout')
    args=parser.parse_args()
    keytool=shutil.which('keytool')
    if not keytool:parser.error('Install a JDK providing keytool first')
    directory=args.directory.expanduser().resolve()
    if directory.is_relative_to(Path(__file__).resolve().parents[2]):parser.error('Use a private signing directory outside the source checkout')
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    if directory.is_symlink() or directory.stat().st_mode & 0o077:parser.error('Signing directory must be private (0700)')
    key=directory/'yichat-development.p12';config=directory/'signing-private.json'
    if key.exists() or config.exists():parser.error('Signing identity already exists; reuse it for upgrades, do not overwrite')
    password=secrets.token_urlsafe(36)
    env=dict(os.environ,YICHAT_DEV_SIGNING_PASSWORD=password)
    subprocess.run([keytool,'-genkeypair','-keystore',str(key),'-storetype','PKCS12',
        '-storepass:env','YICHAT_DEV_SIGNING_PASSWORD','-keypass:env','YICHAT_DEV_SIGNING_PASSWORD',
        '-alias','yichat-development','-keyalg','RSA','-keysize','3072','-validity','10000',
        '-dname','CN=YiChat Development, O=YiChat, C=ZZ','-noprompt'],env=env,check=True,
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    key.chmod(0o600)
    fd=os.open(config,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:json.dump({'storeFile':str(key),'storeType':'PKCS12','keyAlias':'yichat-development','storePassword':password,'keyPassword':password},f)
    print('Created private signing identity. Keep both files private and reuse them for upgrades.')
    print('Configure Gradle to read signing-private.json locally; never commit, log, or bundle it with public source.')


if __name__=='__main__':main()
