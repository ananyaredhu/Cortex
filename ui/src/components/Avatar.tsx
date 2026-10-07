// Port of the Cortex kit's Avatar (design-reference/cortex-design-system/components/core/Avatar.jsx).
export function Avatar({ src, name, size = 32 }: { src?: string; name: string; size?: number }) {
  const initials = name
    .split(" ")
    .map((w) => w[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
  return (
    <span className="avatar" style={{ width: size, height: size, fontSize: Math.round(size * 0.38) }} title={name}>
      {src ? <img src={src} alt="" /> : initials}
    </span>
  );
}
