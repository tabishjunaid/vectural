import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import './index.css';
import { PersonaProvider } from './lib/persona';
import { DepthProvider } from './lib/depth';
import { CitationDrawerProvider } from './components/citation';
import { Landing } from './pages/Landing';
import { Ask } from './pages/Ask';
import { Coverage } from './pages/Coverage';
import { Review } from './pages/Review';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <PersonaProvider>
      <DepthProvider>
        <CitationDrawerProvider>
          <BrowserRouter>
            <Routes>
              <Route path="/" element={<Landing />} />
              <Route path="/ask" element={<Ask />} />
              <Route path="/coverage" element={<Coverage />} />
              <Route path="/review" element={<Review />} />
            </Routes>
          </BrowserRouter>
        </CitationDrawerProvider>
      </DepthProvider>
    </PersonaProvider>
  </StrictMode>,
);
