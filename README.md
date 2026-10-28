A Python command line tool to copy the directory layout from one computer to another without transfering the files.

Vibecoded with ChatGPT and manually reviewied and tweaked.

LICENSE: CC0 1.0 Universal / Public Domain

## Usage
1. Ensure the files in both directories on both computers are the same.
2. Run `layoutcopy export path/to/src/dir path/to/manifest.json` on the source computer.
3. Send the manifest JSON over to the destination computer.
4. Run `layoutcopy apply path/to/dest/dir path/to/manifest.json` on the destination computer. This will perform a *dry run* for previewing layout changes. If you're satisfied with the dry run and ready to apply, add `--execute`: `layoutcopy apply path/to/dest/dir path/to/manifest.json --execute`.

The default hashing algorithm is `blake2b``. You can specify another one with `--hash`. Supported hashing algorithms are the ones listed in `hashlib.algorithms_guaranteed` to ensure portability. CRC32/64 is not available.

The default BLAKE2b should provide a good compromise between speed and hash collision rate. Collisions are detected during the *apply* stage. If a collision occurs (*extremely* unlikely with BLAKE2b, but not impossible), you'll have to resolve it manually, possibly by editing the manifest and moving the files yourself.

