import Nav from './components/Nav'
import Hero from './components/Hero'
import Features from './components/Features'
import HowItWorks from './components/HowItWorks'
import Logos from './components/Logos'
import CTA from './components/CTA'
import Footer from './components/Footer'

export default function Home() {
  return (
    <>
      <Nav />
      <main id="main-content">
        <Hero />
        <Features />
        <HowItWorks />
        <Logos />
        <CTA />
      </main>
      <Footer />
    </>
  )
}
