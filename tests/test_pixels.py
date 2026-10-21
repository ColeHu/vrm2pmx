"""Numerical texture semantics for the base-only material profile."""
import runpy
import tempfile
import unittest
from pathlib import Path
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
API = runpy.run_path(str(ROOT/'scripts/material_auto_pixels.py'))


class BasePixels(unittest.TestCase):
    def test_linear_color_factor_and_alpha_modes(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            pixels = np.full((2,2,4),(.25,.5,.75,.4))
            np.save(folder/'source.npy', pixels)
            for mode in ('OPAQUE','MASK','BLEND'):
                row = dict(key=mode, base_factor=[.8,.6,.4,.4],alpha_mode=mode,alpha_cutoff=.5,
                           textures={'base':dict(path=str(folder/'source.npy'),encoding='linear')})
                result = API['convert_row'](row,folder)
                actual = np.asarray(Image.open(result['base']))/255
                expected = API['srgb'](np.array([.2,.3,.3]))
                np.testing.assert_allclose(actual[0,0,:3],expected,atol=1/255)
                self.assertAlmostEqual(actual[0,0,3],{'OPAQUE':1,'MASK':0,'BLEND':.4}[mode],places=5)
                self.assertEqual(result['material_alpha'],.4 if mode=='BLEND' else 1)
                self.assertIsNone(result['sphere'])
                self.assertIsNone(result['toon'])
            self.assertFalse(list(folder.glob('*sphere*')))
            self.assertFalse(list(folder.glob('*mask.npy')))

    def test_constant_material_generates_colored_base(self):
        with tempfile.TemporaryDirectory() as folder:
            row = dict(key='constant',base_factor=[.2,.4,.6,.8],alpha_mode='BLEND',alpha_cutoff=.5,
                       textures={'base':{}})
            result = API['convert_row'](row,folder)
            image = np.asarray(Image.open(result['base']))/255
            np.testing.assert_allclose(image[0,0,:3],API['srgb'](np.array([.2,.4,.6])),atol=1/255)
            self.assertAlmostEqual(image[0,0,3]*result['material_alpha'],.8)

    def test_transparent_mix_mask_and_inverse(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder)
            np.save(folder/'mask.npy',np.full((2,2,4),(.25,.25,.25,.8)))
            for invert in (False,True):
                row=dict(key=str(invert),base_factor=[1,1,1,.6],alpha_mode='BLEND',alpha_cutoff=.5,base_alpha_used=False,
                         textures={'base':{},'alpha':dict(path=str(folder/'mask.npy'),encoding='linear',channel='grayscale_rgb',invert=invert)})
                result=API['convert_row'](row,folder)
                actual=np.asarray(Image.open(result['base']))[0,0,3]/255
                self.assertAlmostEqual(actual,.75 if invert else .25,delta=1/255)
                self.assertEqual(result['material_alpha'],.6)

    def test_ambiguous_colored_opacity_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder)
            np.save(folder/'mask.npy',np.full((2,2,4),(.1,.4,.8,1)))
            row=dict(key='bad',base_factor=[1,1,1,1],alpha_mode='BLEND',alpha_cutoff=.5,
                     textures={'base':{},'alpha':dict(path=str(folder/'mask.npy'),encoding='linear',channel='grayscale_rgb')})
            with self.assertRaises(ValueError):API['convert_row'](row,folder)


if __name__=='__main__':
    unittest.main()
