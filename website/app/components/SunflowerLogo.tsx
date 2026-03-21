export default function SunflowerLogo({
  size = 28,
  className,
}: {
  size?: number
  className?: string
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      className={className}
      aria-hidden="true"
    >
      {/* Petals */}
      {Array.from({ length: 10 }, (_, i) => {
        const angle = (i * 36 * Math.PI) / 180
        const cx = 16 + Math.cos(angle) * 9
        const cy = 16 + Math.sin(angle) * 9
        return (
          <ellipse
            key={i}
            cx={cx}
            cy={cy}
            rx={4.2}
            ry={2.4}
            fill="#fbbf24"
            transform={`rotate(${i * 36} ${cx} ${cy})`}
          />
        )
      })}
      {/* Center */}
      <circle cx={16} cy={16} r={5.5} fill="#92400e" />
    </svg>
  )
}
