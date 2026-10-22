// Stands in for the `meshoptimizer` package in the bundle (alias in vite.config.ts).
//
// meshoptimizer compiles WebAssembly as soon as it is imported (its index pulls in the encoder,
// decoder, simplifier, clusterizer and tangents), and our CSP has no 'wasm-unsafe-eval', so each of
// them would fail on load. Cesium uses only `MeshoptDecoder`, to decode meshopt-compressed glTF and
// 3D-tiles terrain; the globe loads neither. Keeping the CSP strict is worth this stub: if a future
// globe feature needs those formats, revisit both together.

const unavailable = (): never => {
  throw new Error('meshopt decoding is not part of this build (see meshoptStub.ts)')
}

export const MeshoptDecoder = {
  supported: false,
  ready: Promise.resolve(),
  useWorkers: () => {},
  decodeVertexBuffer: unavailable,
  decodeIndexBuffer: unavailable,
  decodeIndexSequence: unavailable,
  decodeGltfBuffer: unavailable,
  decodeGltfBufferAsync: unavailable,
}
