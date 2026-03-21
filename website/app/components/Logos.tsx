'use client'

import { motion } from 'framer-motion'

const techStack = [
  { name: 'Open WebUI', color: '#60a5fa' },
  { name: 'Ollama', color: '#a78bfa' },
  { name: 'FastAPI', color: '#34d399' },
  { name: 'Docker', color: '#60a5fa' },
  { name: 'PostgreSQL', color: '#818cf8' },
  { name: 'Redis', color: '#f87171' },
  { name: 'Python', color: '#fbbf24' },
  { name: 'Prometheus', color: '#fb923c' },
  { name: 'Grafana', color: '#f472b6' },
  { name: 'SQLCipher', color: '#34d399' },
  { name: 'Argon2id', color: '#a78bfa' },
  { name: 'Celery', color: '#4ade80' },
]

// Duplicate for seamless loop
const items = [...techStack, ...techStack]

export default function Logos() {
  return (
    <section
      className="relative py-24 overflow-hidden border-y border-white/[0.06]"
      aria-label="Technologies powering snflwr.ai"
    >
      {/* Subtle fade masks on sides */}
      <div
        className="absolute left-0 top-0 bottom-0 w-24 z-10 pointer-events-none"
        style={{
          background: 'linear-gradient(to right, #070708, transparent)',
        }}
        aria-hidden="true"
      />
      <div
        className="absolute right-0 top-0 bottom-0 w-24 z-10 pointer-events-none"
        style={{
          background: 'linear-gradient(to left, #070708, transparent)',
        }}
        aria-hidden="true"
      />

      <div className="relative z-10 max-w-6xl mx-auto px-6 mb-10 text-center">
        <motion.p
          initial={{ opacity: 0, y: 10 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.4 }}
          className="text-xs font-bold uppercase tracking-[0.15em] text-white/30 mb-2"
        >
          Powered by battle-tested open source
        </motion.p>
      </div>

      {/* Marquee */}
      <div className="overflow-hidden">
        <div
          className="animate-marquee flex items-center gap-4 w-max"
          aria-hidden="true"
        >
          {items.map((tech, i) => (
            <div
              key={i}
              className="flex items-center gap-2.5 px-5 py-2.5 rounded-full border border-white/[0.08] bg-white/[0.03] whitespace-nowrap select-none"
            >
              <span
                className="w-2 h-2 rounded-full flex-shrink-0"
                style={{ backgroundColor: tech.color, opacity: 0.8 }}
              />
              <span className="text-sm font-medium text-white/50">{tech.name}</span>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
