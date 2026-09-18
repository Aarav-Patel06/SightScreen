/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Without this, Next walks up looking for a lockfile and can pick one from
  // outside the repo entirely - on this machine it chose the one in the
  // Windows home directory. Vercel's Root Directory = web makes that
  // unlikely there, but a build whose traced root depends on what happens to
  // sit in a parent directory is not a build you can reproduce.
  outputFileTracingRoot: import.meta.dirname,
};

export default nextConfig;
