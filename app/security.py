import os, hashlib, hmac

def hash_password(password:str)->str:
    salt=os.urandom(16)
    digest=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,180000)
    return salt.hex()+':'+digest.hex()

def verify_password(password:str,stored:str)->bool:
    try:
        salt_hex,digest_hex=stored.split(':',1)
        test=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt_hex),180000).hex()
        return hmac.compare_digest(test,digest_hex)
    except Exception:
        return False
