# The two ports that remain, scoped with what has been measured

`docs/boot-audit.md` records why these two are the only things left between
this kernel and an Android UI. This file is the work order: what is known,
what is not, and what the next attempt should touch. Nothing here is
implemented - the point is that the unknowns are now small and named.

## B-1: `/dev/qseecom` (what `/data` needs)

### What is already measured

* Callers: `qseecomd` (`class core`, `on init`), `libQSEEComAPI.so`,
  `libqtikeymint.so` (loaded by `android.hardware.security.keymind-service-qti`,
  `class early_hal`). The first two open `"/dev/qseecom"`; the failure strings
  are in `boot-audit.md`.
* The ioctl ABI, from the ROM's own `libQSEEComAPI.so` (17 commands, type
  `0x97`):

  ```
  0xc0189701/24  0x9702/0   0xc0209703/32  0xc0409704/64  0x9705/0  0x9706/0
  0xc0549707/84  0xc0189708/24  0x9709/0   0xc004970a/4   0x970e/0  0xc048970f/72
  0xc0289710/40  0xc0249711/36  0xc0089712/8  0xc0309715/48  0xc0449718/68
  ```

  `nr 3` (32-byte argument) is on `QSEECom_send_cmd`'s path; the vendor driver's
  dispatcher tests `0xc0309715` and `0xc0309724` first.
* The `APP_LOAD` argument (`nr 1`, 24 bytes) is built as
  `{u32, u32, char __user *app_name, u32}` - offsets 0, 4, 8, 16, with the name
  as a *userspace pointer*, not an inline array. So loading is by name.
* `qseecomd` itself carries no `/vendor/firmware`, `.mdt` or `.b0` string
  (216 strings in total), so it is not the component that reads TA images.
* TZ side: `tz.mbn` contains `KeymasterLite_*` and 618 QSEE strings, and the
  `modem` partition's `image/` carries the trustlets (`mfido.mdt`,
  `mkeybag.mdt`, `secauth.mdt`, `soter64.mdt`, `widevine.mbn`).
* Kernel side, exported already: `qcom_scm_qseecom_app_get_id(app_name, &id)`
  and `qcom_scm_qseecom_app_send(app_id, req, req_size, rsp, rsp_size)`
  (`qcom_scm.c:2231`, `:2283`), with `CONFIG_QCOM_TZMEM=y` for the buffers.

### What is not measured

1. Whether TZ on this device answers `APP_GET_ID` for the KeyMint app name -
   i.e. whether the TA is TZ-internal (the `KeymasterLite` strings suggest a
   built-in KM service) or must be loaded from `mfido.mdt` by a path mainline
   does not export (`LOAD_APP` with an image, `SET_MEM_PARAM`, listeners).
2. ~~The meaning of the four `APP_LOAD` fields.~~ **Resolved** by reading
   `__QSEECom_get_handle` at `0xb3a0-0xb3d8`. The argument is 24 bytes built on
   the caller's stack as `{u32, u32, char __user *name, u32 name_len}`:
   `stp w22, w8, [sp,#16]` (offsets 0 and 4, `w8 = [x19+24]`), `str x0, [sp,#24]`
   (offset 8, the name pointer from `[x19]`), `str w21, [sp,#32]` (offset 16).
   Just before the ioctl the library `memset`s that name buffer with
   `w2 = w21`, so `w21` is its length and the kernel copies a
   length-delimited name from user memory.
3. ~~The `nr 3` argument layout.~~ **Resolved** at `0xb690-0xb744`; 32 bytes,
   `{void *req; u32 req_len; void *rsp; u32 rsp_len}` at offsets 0, 8, 16, 24.
   The response pointer is literally `req + req_len` (`add x9, x8, w21, uxtw`),
   and both lengths are rounded up to 64 (`and w21, w8, #0xffffffc0`) when a
   flag in the internal handle is 0x14 - that is the 64-byte alignment TZ
   requires for shared buffers.

So both ioctls the KeyMint path needs map cleanly onto
`qcom_scm_qseecom_app_send(app_id, req, req_size, rsp, rsp_size)` with tzmem
copies, and the only genuinely unknown step is whether TZ answers the app
lookup for the KeyMint name.

### Suggested first attempt

A DT-gated chardev implementing `nr 1` and `nr 3` only, mapping
`APP_GET_ID(name)` and `APP_SEND(id, req, rsp)` onto tzmem buffers, returning
`-ENOSYS` for everything else. Keep it **disabled in the config** until a
device boot can be watched, because a shim that binds and then blocks inside
an early HAL is worse than one that fails cleanly with `ENOENT`.

## B-2: the graphics ABI (what SurfaceFlinger needs)

### What is already measured

* `libgralloccore.so` opens **`/dev/qti-smmu-proxy`**, which only
  `smmu_proxy_dlkm.ko` provides; that module exports `smmu_proxy_create_dev`,
  `smmu_proxy_unmap` and friends (10 named functions).
* `libsdedrm.so` (composer side) contains no `/dev` or `/sys` literal - it goes
  through libdrm, and mainline `msm` does not implement the vendor SDE ioctls.
  That last step is inference from stub behaviour, not a decoded contract.
* Mainline has `DRM_MSM`, the SM4450 DPU catalogue (`dpu_8_6_sm4450.h`) and the
  DSI cfg for hardware version 2.6, so the kernel can drive the panel as a
  console. What it cannot do is serve the ROM's gralloc/composer ABI.

### What is not measured

1. The SDE ioctl set the ROM's composer actually issues - obtainable the same
   way as the QSEECOM table: collect `ioctl(fd, cmd, arg)` sites from
   `libsdedrm.so` and the composer service.
2. Whether the allocator can be satisfied by a plain dma-buf heap if a
   `/dev/qti-smmu-proxy`-compatible node were provided (the proxy is a
   dma-mapping indirection; a stub that forwards to the DMA API may be enough
   for allocation, even if the vendor's secure path is not).

### Why this is bigger than B-1

`/data` is a key-management problem with a small ABI. The UI is a whole
display stack: allocator, composer, and the SDE UAPI. Even a perfect kernel
side does not remove the need for the ROM's userspace to find its ioctls.

## Order of work

1. Finish reading `__QSEECom_get_handle` and `QSEECom_send_cmd` to write down the
   two argument layouts (no device needed).
2. Build the DT-gated shim, keep it off, and boot it on the phone with a kernel
   command line that enables verbose logging.
3. Only if TZ answers the app lookup, enable it by default and re-test `/data`.
4. Collect the SDE ioctl set next; decide from its size whether the graphics
   port is viable or whether the honest end state is "kernel boots, Android
   does not draw".
