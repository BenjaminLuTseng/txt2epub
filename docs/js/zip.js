// Minimal ZIP writer. EPUB needs one stored entry (mimetype) first and
// deflated entries after, which is small enough not to justify a dependency.
// Deflate comes from CompressionStream('deflate-raw'), built into the browser.

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

function crc32(bytes) {
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

export const deflateSupported = typeof CompressionStream !== 'undefined';

async function deflateRaw(bytes) {
  if (!deflateSupported) return null;
  const stream = new Blob([bytes]).stream().pipeThrough(new CompressionStream('deflate-raw'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

function dosDateTime(date = new Date()) {
  const time =
    (date.getHours() << 11) | (date.getMinutes() << 5) | (Math.floor(date.getSeconds() / 2) & 0x1f);
  const day = ((date.getFullYear() - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate();
  return { time, day };
}

class Writer {
  constructor() {
    this.parts = [];
    this.length = 0;
  }
  push(bytes) {
    this.parts.push(bytes);
    this.length += bytes.length;
  }
  u16(n) {
    this.push(new Uint8Array([n & 0xff, (n >> 8) & 0xff]));
  }
  u32(n) {
    this.push(new Uint8Array([n & 0xff, (n >> 8) & 0xff, (n >> 16) & 0xff, (n >>> 24) & 0xff]));
  }
  blob(type) {
    return new Blob(this.parts, { type });
  }
}

/**
 * @param {{name: string, data: Uint8Array|string, store?: boolean}[]} entries
 * @returns {Promise<Blob>}
 */
export async function makeZip(entries) {
  const encoder = new TextEncoder();
  const out = new Writer();
  const central = [];
  const { time, day } = dosDateTime();

  for (const entry of entries) {
    const nameBytes = encoder.encode(entry.name);
    const raw = typeof entry.data === 'string' ? encoder.encode(entry.data) : entry.data;
    const crc = crc32(raw);

    let payload = raw;
    let method = 0;
    if (!entry.store) {
      const deflated = await deflateRaw(raw);
      // Only take the compressed form if it actually helped.
      if (deflated && deflated.length < raw.length) {
        payload = deflated;
        method = 8;
      }
    }

    const offset = out.length;
    out.u32(0x04034b50);
    out.u16(method ? 20 : 10);
    out.u16(0x0800); // bit 11: filenames are UTF-8
    out.u16(method);
    out.u16(time);
    out.u16(day);
    out.u32(crc);
    out.u32(payload.length);
    out.u32(raw.length);
    out.u16(nameBytes.length);
    out.u16(0);
    out.push(nameBytes);
    out.push(payload);

    central.push({ nameBytes, crc, method, compressed: payload.length, size: raw.length, offset });
  }

  const centralStart = out.length;
  for (const e of central) {
    out.u32(0x02014b50);
    out.u16(0x031e); // made by: UNIX, zip 3.0
    out.u16(e.method ? 20 : 10);
    out.u16(0x0800);
    out.u16(e.method);
    out.u16(time);
    out.u16(day);
    out.u32(e.crc);
    out.u32(e.compressed);
    out.u32(e.size);
    out.u16(e.nameBytes.length);
    out.u16(0); // extra
    out.u16(0); // comment
    out.u16(0); // disk
    out.u16(0); // internal attrs
    out.u32(0); // external attrs
    out.u32(e.offset);
    out.push(e.nameBytes);
  }

  // Measure the central directory *before* writing the end record: `out.length`
  // keeps growing as the record is appended, so reading it inline would count
  // the record's own bytes and leave strict parsers seeking to the wrong offset.
  const centralSize = out.length - centralStart;

  out.u32(0x06054b50);
  out.u16(0); // disk number
  out.u16(0); // disk with central directory
  out.u16(central.length);
  out.u16(central.length);
  out.u32(centralSize);
  out.u32(centralStart);
  out.u16(0); // comment length

  return out.blob('application/epub+zip');
}
