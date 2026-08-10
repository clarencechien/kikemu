import './styles/tokens.css';
import './styles/base.css';
import './styles/login.css';
import './styles/listen.css';
import { initLogin } from './ui/login';
import { initListen } from './ui/listen';
import { initHistory } from './ui/history';
import { initPwa } from './ui/pwa';
import { playDemo } from './ui/demo';

const listen = initListen(() => void playDemo());
initLogin({
  onAuthed: me => listen.onAuthed(me),
  onPreview: () => void playDemo(), // 預覽走 demo.ts,結構上碰不到 WS 與麥克風
});
initHistory(listen);
initPwa();
