import platform
if platform.system() == 'Windows':
    # -*- mode: python -*-
    a = Analysis(['pcbasic.py'],
                 pathex=['C:\\Documents and Settings\\rob\\My Documents\\Projects\\pc-basic_distributions\\pc-basic'],
                 hiddenimports=[],
                 hookspath=None,
                 runtime_hooks=None)
    pyz = PYZ(a.pure)
    exe = EXE(pyz,
              a.scripts,
              exclude_binaries=True,
              name='pcbasic.exe',
              debug=False,
              strip=None,
              upx=True,
              console=False , 
		  icon='C:\\Documents and Settings\\rob\\My Documents\\Projects\\pc-basic_distributions\\pc-basic\\resources\\pcbasic.ico')
    coll = COLLECT(exe,
                   a.binaries,
                   a.zipfiles,
                   a.datas,
                   Tree('cpi', prefix='cpi'),
                   [
            ('INFO.BAS', 'C:\\Documents and Settings\\rob\\My Documents\\Projects\\pc-basic_distributions\\pc-basic\\INFO.BAS', 'DATA'),
            ('ABOUT', 'C:\\Documents and Settings\\rob\\My Documents\\Projects\\pc-basic_distributions\\pc-basic\\ABOUT', 'DATA'),
            ('GPL3', 'C:\\Documents and Settings\\rob\\My Documents\\Projects\\pc-basic_distributions\\pc-basic\\/GPL3', 'DATA'),
            ('HELP', 'C:\\Documents and Settings\\rob\\My Documents\\Projects\\pc-basic_distributions\\pc-basic\\HELP', 'DATA'),
            ('CC-BY-SA', 'C:\\Documents and Settings\\rob\\My Documents\\Projects\\pc-basic_distributions\\pc-basic\\CC-BY-SA', 'DATA'),
            ('COPYING', 'C:\\Documents and Settings\\rob\\My Documents\\Projects\\pc-basic_distributions\\pc-basic\\COPYING', 'DATA'),
                   ],
                   strip=None,
                   upx=True,
                   name='pcbasic')


elif platform.system() == 'Linux':
    # -*- mode: python -*-
    a = Analysis(['pcbasic.py'],
                 pathex=['/home/rob/Projects/basic-project/pc-basic'],
                 hiddenimports=[],
                 hookspath=None,
                 runtime_hooks=None)
    pyz = PYZ(a.pure)
    exe = EXE(pyz,
              a.scripts,
              exclude_binaries=True,
              name='pcbasic',
              debug=False,
              strip=None,
              upx=True,
              console=True )
    coll = COLLECT(exe,
                   a.binaries - [
                        ('libcrypto.so.1.0.0', None, None),
                        ('libfreetype.so.6', None, None),
                        ('libncursesw.so.5', None, None),
                        ('libsmpeg-0.4.so.0', None, None),
                        ('libsndfile.so.1', None, None), 
                        ('libvorbisenc.so.2', None, None),
                        ('libvorbis.so.0', None, None),
                        ('libvorbisfile.so.3', None, None),
                        ('libogg.so.0', None, None),
                        ('libpng12.so.0', None, None),
                        ('libmikmod.so.2', None, None),
                        ('libcaca.so.0', None, None),
                        ('libjpeg.so.8', None, None),
                        ('libFLAC.so.8', None, None),
                        ('libblas.so.3gf', None, None),
                        ('liblapack.so.3gf', None, None),
                        ('libgfortran.so.3', None, None),
                        ('libslang.so.2', None, None),
                        ('libtiff.so.4', None, None),
                        ('libquadmath.so.0', None, None),
                        ('libssl.so.1.0.0', None, None),
                        ('libbz2.so.1.0', None, None),
                        ('libdbus-1.so.3', None, None),
                        ('libstdc++.so.6', None, None),
                        ('libreadline.so.6', None, None), # though this may be useful in future for dumbterm mode 
                        ('libtinfo.so.5', None, None),
                        ('libexpat.so.1', None, None),
                        ('libmad.so.0', None, None),
                        ('libjson.so.0', None, None),
                        ('libgcc_s.so.1', None, None),
                        ('libasyncns.so.0', None, None),
                   ],
                   a.zipfiles,
                   a.datas,
                   Tree('cpi', prefix='cpi'),
                   [
                        ('INFO.BAS', '/home/rob/Projects/basic-project/pc-basic/INFO.BAS', 'DATA'),
                        ('ABOUT', '/home/rob/Projects/basic-project/pc-basic/ABOUT', 'DATA'),
                        ('GPL3', '/home/rob/Projects/basic-project/pc-basic/GPL3', 'DATA'),
                        ('HELP', '/home/rob/Projects/basic-project/pc-basic/HELP', 'DATA'),
                        ('CC-BY-SA', '/home/rob/Projects/basic-project/pc-basic/CC-BY-SA', 'DATA'),
                        ('COPYING', '/home/rob/Projects/basic-project/pc-basic/COPYING', 'DATA'),
                   ],
                   strip=None,
                   upx=True,
                   name='pcbasic')
                   
                   
elif platform.system() == 'Darwin':
	# -*- mode: python -*-
	a = Analysis(['pcbasic.py'],
             pathex=['/Users/rob/pc-basic'],
             hiddenimports=[],
             hookspath=None,
             runtime_hooks=None)
	pyz = PYZ(a.pure)
	exe = EXE(pyz,
          a.scripts,
          exclude_binaries=True,
          name='pcbasic',
          debug=False,
          strip=None,
          upx=True,
          console=False )
	coll = COLLECT(exe,
               a.binaries,
               a.zipfiles,
               a.datas,
               Tree('cpi', prefix='cpi'),
               [
                        ('INFO.BAS', '/Users/rob/pc-basic/INFO.BAS', 'DATA'),
                        ('ABOUT', '/Users/rob/pc-basic/ABOUT', 'DATA'),
                        ('GPL3', '/Users/rob/pc-basic/GPL3', 'DATA'),
                        ('HELP', '/Users/rob/pc-basic/HELP', 'DATA'),
                        ('CC-BY-SA', '/Users/rob/pc-basic/CC-BY-SA', 'DATA'),
                        ('COPYING', '/Users/rob/pc-basic/COPYING', 'DATA'),
               ],
               strip=None,
               upx=True,
               name='pcbasic')
	app = BUNDLE(coll,
             name='pcbasic.app',
             icon='/Users/rob/pc-basic/resources/pcbasic.icns')

