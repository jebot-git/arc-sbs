"""Real GPU parity tests; skip discovery only on machines without an A770."""
import numpy as np
import pytest

from arc_sbs.stereo import render_sbs


@pytest.fixture(scope="module")
def gpu_renderer():
    cl = pytest.importorskip("pyopencl")
    from arc_sbs.gpu_stereo import OpenCLRenderer, gpu_devices
    try:
        available = gpu_devices()
    except cl.Error:
        pytest.skip("OpenCL runtime unavailable")
    if not any("A770" in d.name and "Intel" in d.vendor for d in available):
        pytest.skip("A770 unavailable")
    # Build or execution failures on an available A770 must fail the tests.
    return OpenCLRenderer()


@pytest.mark.parametrize("layout", ["half", "full"])
@pytest.mark.parametrize("swap", [False, True])
@pytest.mark.parametrize("case", ["random", "silhouette", "neutral", "ties", "negative"])
def test_gpu_matches_cpu_on_visibility_holes_rounding_and_eye_order(gpu_renderer, layout, swap, case):
    rng = np.random.default_rng(289)
    frame = rng.integers(0,256,(24,160,3),dtype=np.uint8)
    if case == "random":
        depth = rng.random(frame.shape[:2],dtype=np.float32)
    elif case == "silhouette":
        depth = np.zeros(frame.shape[:2],dtype=np.float32)
        depth[:, 40:120] = 1
        frame[:, 0] = 0  # Valid black pixels must not be interpreted as holes.
    elif case == "neutral":
        depth = np.full(frame.shape[:2],0.5,dtype=np.float32)
    elif case == "ties":
        depth = np.tile(np.array([0.25,0.5,0.75,1],dtype=np.float32), (24,40))
    else:
        depth = rng.random(frame.shape[:2],dtype=np.float32)*4-2
    actual = gpu_renderer.render(frame,depth,strength=0.1,layout=layout,swap=swap)
    expected = render_sbs(frame,depth,strength=0.1,layout=layout,swap=swap)
    np.testing.assert_array_equal(actual,expected)


def test_gpu_buffers_are_cleared_between_frames_and_resized(gpu_renderer):
    for h,w in [(24,80),(24,80),(16,42),(24,80)]:
        frame = np.full((h,w,3),71,dtype=np.uint8)
        depth = np.ones((h,w),dtype=np.float32)
        gpu_renderer.render(frame,depth,strength=0.1,convergence=0,layout="full")
        frame[:] = 0
        np.testing.assert_array_equal(gpu_renderer.render(frame,depth,strength=0),np.zeros_like(frame))


def test_gpu_rejects_invalid_depth_before_enqueue(gpu_renderer):
    with pytest.raises(ValueError,match="finite"):
        gpu_renderer.render(np.zeros((2,4,3),np.uint8),np.full((2,4),np.nan))


@pytest.mark.parametrize("strength,convergence", [(0.1,0), (0.1,1), (0.025,0.25), (0.0125,0.5)])
def test_gpu_extreme_planes_and_half_pixel_ties(gpu_renderer, strength, convergence):
    rng = np.random.default_rng(511)
    frame = rng.integers(0,256,(12,160,3),dtype=np.uint8)
    depth = np.zeros((12,160),dtype=np.float32)
    depth[:,::2] = -0.0
    depth[6:] = 1
    np.testing.assert_array_equal(
        gpu_renderer.render(frame,depth,strength,convergence,"full"),
        render_sbs(frame,depth,strength,convergence,"full"))


@pytest.mark.parametrize("layout", ["half", "full"])
def test_gpu_preserves_sub_ulp_offsets_around_half_pixels_at_hd_width(gpu_renderer, layout):
    # At x=198, float32(198-11.499999) erases the small offset above .5.
    # CPU int32 + float32 uses float64, so the rounded destination is 187.
    rng = np.random.default_rng(717)
    frame = rng.integers(0,256,(2,1920,3),dtype=np.uint8)
    depth = np.full((2,1920),np.float32(0.020833367),dtype=np.float32)
    np.testing.assert_array_equal(gpu_renderer.render(frame,depth,layout=layout),
                                  render_sbs(frame,depth,layout=layout))


@pytest.mark.parametrize('layout', ['half', 'full'])
@pytest.mark.parametrize('swap', [False, True])
def test_gpu_preserves_bgra_visibility_and_alpha_weighted_half_sbs(gpu_renderer, layout, swap):
    rng = np.random.default_rng(1801)
    for channels in (3, 4, 3, 4):  # reallocate channel-dependent buffers safely
        frame = rng.integers(0, 256, (16, 160, channels), dtype=np.uint8)
        if channels == 4:
            frame[:, :30, 3] = 0
            frame[::3, :, 3] = 0
            frame[1::3, :, 3] = 128
        depth = rng.random((16, 160), dtype=np.float32)
        np.testing.assert_array_equal(gpu_renderer.render(frame, depth, strength=.1, layout=layout, swap=swap),
                                      render_sbs(frame, depth, strength=.1, layout=layout, swap=swap))
