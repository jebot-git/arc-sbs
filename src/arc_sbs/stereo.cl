// Generic depth-image-based rendering. No relaxed math: preserve CPU rounding.
#pragma OPENCL EXTENSION cl_khr_int64_base_atomics : enable
#pragma OPENCL EXTENSION cl_khr_int64_extended_atomics : enable
#pragma OPENCL FP_CONTRACT OFF

__kernel void clear_keys(__global ulong *keys, const int count) {
    int i = get_global_id(0);
    if (i < 2*count) keys[i] = 0;
}

__kernel void splat(__global const float *depth, __global ulong *keys,
                    const int width, const int count,
                    const float span, const float convergence) {
    int i = get_global_id(0);
    int eye = get_global_id(1);
    if (i >= count) return;
    int x = i % width;
    float d = depth[i];
    if (d == 0.0f) d = 0.0f;  // +0/-0 compare equal in the CPU z-buffer.
    float disparity = (clamp(d, 0.0f, 1.0f)-convergence)*span;
    float shift = disparity*0.5f;
    // NumPy promotes int32 x + float32 shift to float64. Adding in float32
    // would erase small offsets around half-pixel boundaries at HD widths.
    // Split the shift and round using the integer target parity instead;
    // this preserves reference rounding without requiring GPU FP64.
    float signed_shift = eye == 0 ? shift : -shift;
    int whole = convert_int_rtn(signed_shift);
    float fraction = signed_shift - convert_float(whole);
    int target = x + whole;
    if (fraction > 0.5f || (fraction == 0.5f && (target & 1))) ++target;
    if (target < 0 || target >= width) return;
    // Order every finite IEEE float, including negative values. The low word
    // breaks equal-depth ties by source index, matching CPU's last-source win.
    uint bits = as_uint(d);
    uint ordered = bits ^ ((bits & 0x80000000U) ? 0xffffffffU : 0x80000000U);
    ulong key = ((ulong)ordered << 32) | (uint)(i+1);
    atom_max(&keys[eye*count + (i-x) + target], key);
}

// Return source index for a visible destination or its background-filled hole.
int resolve(__global const ulong *keys, __global const float *depth,
            const int eye, const int x, const int y,
            const int width, const int count) {
    int row = eye*count + y*width;
    ulong k = keys[row+x];
    if (k) return (int)((uint)k)-1;
    int left = x-1, right = x+1;
    while (left >= 0 && !keys[row+left]) --left;
    while (right < width && !keys[row+right]) ++right;
    if (left < 0 && right >= width) return -1;
    if (left < 0) return (int)((uint)keys[row+right])-1;
    if (right >= width) return (int)((uint)keys[row+left])-1;
    int l = (int)((uint)keys[row+left])-1;
    int r = (int)((uint)keys[row+right])-1;
    return depth[l] <= depth[r] ? l : r;
}

__kernel void pack(__global const uchar *frame, __global const float *depth,
                   __global const ulong *keys, __global uchar *output,
                   const int width, const int height, const int half_sbs,
                   const int swap) {
    int eye_width = half_sbs ? width/2 : width;
    int out_width = 2*eye_width;
    int i = get_global_id(0);
    if (i >= out_width*height) return;
    int x = i % out_width;
    int y = i / out_width;
    int eye = (x / eye_width) ^ swap;
    x %= eye_width;
    int a = resolve(keys, depth, eye, half_sbs ? x*2 : x, y, width, width*height);
    int b = half_sbs ? resolve(keys, depth, eye, x*2+1, y, width, width*height) : a;
    for (int c = 0; c < 3; ++c) {
        float va = a < 0 ? 0.0f : (float)frame[3*a+c];
        float vb = b < 0 ? 0.0f : (float)frame[3*b+c];
        output[3*i+c] = convert_uchar_sat_rte((va+vb)*0.5f);
    }
}
