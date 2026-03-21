'use client'

import { motion, useReducedMotion } from 'framer-motion'

const flowers = [
  { x: '8%', y: '12%', size: 56, opacity: 0.75, dur: 22, dx: [-20, 30, -15, 0], dy: [-24, 20, -16, 0] },
  { x: '85%', y: '25%', size: 44, opacity: 0.75, dur: 26, dx: [0, -28, 18, 0], dy: [18, -22, 14, 0] },
  { x: '15%', y: '55%', size: 48, opacity: 0.75, dur: 18, dx: [-18, 25, -10, 0], dy: [-20, 16, -12, 0] },
  { x: '78%', y: '68%', size: 40, opacity: 0.75, dur: 24, dx: [0, -24, 20, 0], dy: [16, -18, 22, 0] },
]

export default function FloatingSunflowers() {
  const prefersReducedMotion = useReducedMotion()

  return (
    <div className="fixed inset-0 pointer-events-none z-0" aria-hidden="true">
      {flowers.map((f, i) => (
        <motion.span
          key={i}
          className="absolute text-lg select-none"
          style={{ left: f.x, top: f.y, fontSize: f.size, opacity: f.opacity }}
          animate={
            prefersReducedMotion
              ? {}
              : {
                  x: f.dx,
                  y: f.dy,
                  rotate: [0, 8, -6, 0],
                }
          }
          transition={
            prefersReducedMotion
              ? {}
              : {
                  duration: f.dur,
                  repeat: Infinity,
                  ease: 'easeInOut',
                }
          }
        >
          🌻
        </motion.span>
      ))}
    </div>
  )
}
