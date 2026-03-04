import tempfile, shutil, os
from pathlib import Path
from storage.database import DatabaseManager

temp_dir = tempfile.mkdtemp()
db_path = Path(temp_dir)/"test.db"
print('temp_dir', temp_dir)
db = DatabaseManager(db_path)
print('created manager')
db.initialize_database()
print('initialized')
try:
    shutil.rmtree(temp_dir)
    print('removed temp dir - SUCCESS')
except Exception as e:
    print('remove failed:', e)
    # Try to inspect open handles by opening the file
    try:
        open(db_path,'rb').close()
        print('open success')
    except Exception as e2:
        print('open failed:', e2)
    # Attempt to force gc and close
    import gc, time
    gc.collect()
    time.sleep(0.05)
    try:
        db.close()
        print('db.close() called')
    except Exception as e3:
        print('db.close failed', e3)
    try:
        shutil.rmtree(temp_dir)
        print('removed temp dir after cleanup - SUCCESS')
    except Exception as e4:
        print('still failed:', e4)
