import {StrictMode} from 'react'
import {createRoot} from 'react-dom/client'
import NOCPreview from './NOCPreview'
import './preview.css'
createRoot(document.getElementById('root')!).render(<StrictMode><NOCPreview /></StrictMode>)
