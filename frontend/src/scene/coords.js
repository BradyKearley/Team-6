// The server uses x/y on the floor and z up (meters). Three.js uses y up.
// Map (x, y, z) -> (x, z, -y) so the axes stay right-handed.
export const toScene = (x, y, z) => [x, z, -y];
export const fromScene = (p) => ({ x: p.x, y: -p.z, z: p.y });
