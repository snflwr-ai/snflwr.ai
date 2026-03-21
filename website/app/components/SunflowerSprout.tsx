'use client'

import { motion, AnimatePresence } from 'framer-motion'

export default function SunflowerSprout({
  isHovered,
  reducedMotion,
}: {
  isHovered: boolean
  reducedMotion: boolean
}) {
  const d = reducedMotion ? 0 : undefined

  return (
    <div className="absolute bottom-full left-1/2 -translate-x-1/2 pointer-events-none">
      <AnimatePresence>
        {isHovered && (
          <motion.svg
            key="sprout"
            width={40}
            height={56}
            viewBox="0 0 40 56"
            fill="none"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: d ?? 0.15 }}
          >
            {/* Stem */}
            <motion.path
              d="M20 56 V20"
              stroke="#22c55e"
              strokeWidth={2.5}
              strokeLinecap="round"
              initial={{ pathLength: 0 }}
              animate={{ pathLength: 1 }}
              exit={{ pathLength: 0 }}
              transition={{ duration: d ?? 0.35, ease: 'easeOut' }}
            />

            {/* Left leaf */}
            <motion.path
              d="M20 40 Q12 36 10 30 Q14 34 20 36"
              fill="#22c55e"
              initial={{ scale: 0, originX: '50%', originY: '100%' }}
              animate={{ scale: 1 }}
              exit={{ scale: 0 }}
              transition={{ duration: d ?? 0.2, delay: d ?? 0.2 }}
            />

            {/* Right leaf */}
            <motion.path
              d="M20 34 Q28 30 30 24 Q26 28 20 30"
              fill="#22c55e"
              initial={{ scale: 0, originX: '50%', originY: '100%' }}
              animate={{ scale: 1 }}
              exit={{ scale: 0 }}
              transition={{ duration: d ?? 0.2, delay: d ?? 0.25 }}
            />

            {/* Flower head — petals */}
            <motion.g
              initial={{ scale: 0 }}
              animate={{ scale: 1 }}
              exit={{ scale: 0 }}
              transition={
                d !== undefined
                  ? { duration: 0 }
                  : { type: 'spring', stiffness: 400, damping: 12, delay: 0.35 }
              }
              style={{ transformOrigin: '20px 16px' }}
            >
              {Array.from({ length: 8 }, (_, i) => {
                const angle = (i * 45 * Math.PI) / 180
                const cx = 20 + Math.cos(angle) * 6
                const cy = 16 + Math.sin(angle) * 6
                return (
                  <ellipse
                    key={i}
                    cx={cx}
                    cy={cy}
                    rx={3}
                    ry={1.8}
                    fill="#fbbf24"
                    transform={`rotate(${i * 45} ${cx} ${cy})`}
                  />
                )
              })}
              <circle cx={20} cy={16} r={3.5} fill="#92400e" />
            </motion.g>
          </motion.svg>
        )}
      </AnimatePresence>
    </div>
  )
}
