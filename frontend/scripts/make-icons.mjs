// Renders the app icons from one SVG: node scripts/make-icons.mjs
// Uses sharp, which Next.js already installs for image optimisation.
import { mkdir, writeFile } from "node:fs/promises";
import sharp from "sharp";

// A loaded bar seen from the side: two orange plates on a gold bar.
const mark = (pad) => `
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <rect width="512" height="512" rx="${pad ? 0 : 112}" fill="#0e1116"/>
  <g transform="translate(256 256) scale(${pad ? 0.72 : 0.9}) translate(-256 -256)">
    <rect x="56" y="232" width="400" height="48" rx="12" fill="#ffb020"/>
    <rect x="150" y="96" width="72" height="320" rx="20" fill="#ff5a36"/>
    <rect x="290" y="96" width="72" height="320" rx="20" fill="#ff5a36"/>
  </g>
</svg>`;

await mkdir("public/icons", { recursive: true });
const plain = Buffer.from(mark(false));
const maskable = Buffer.from(mark(true));
for (const size of [192, 512]) {
  await sharp(plain).resize(size, size).png().toFile(`public/icons/icon-${size}.png`);
}
await sharp(maskable).resize(512, 512).png().toFile("public/icons/icon-maskable-512.png");
await sharp(plain).resize(180, 180).png().toFile("app/apple-icon.png");
await writeFile("public/icons/icon.svg", plain);
console.log("icons written");
