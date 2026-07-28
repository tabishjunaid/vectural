import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import './index.css';
import { PersonaProvider } from './lib/persona';
import { DepthProvider } from './lib/depth';
import { ModelProvider } from './lib/model';
import { CitationDrawerProvider } from './components/citation';
import { Landing } from './pages/Landing';
import { Ask } from './pages/Ask';
import { Compare } from './pages/Compare';
import { Coverage } from './pages/Coverage';
import { Review } from './pages/Review';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <PersonaProvider>
      <DepthProvider>
        <ModelProvider>
          <CitationDrawerProvider>
            <BrowserRouter>
              <Routes>
                <Route path="/" element={<Landing />} />
                <Route path="/ask" element={<Ask />} />
                <Route path="/compare" element={<Compare />} />
                <Route path="/coverage" element={<Coverage />} />
                <Route path="/review" element={<Review />} />
              </Routes>
            </BrowserRouter>
          </CitationDrawerProvider>
        </ModelProvider>
      </DepthProvider>
    </PersonaProvider>
  </StrictMode>,
);
