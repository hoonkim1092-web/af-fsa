/**
 * Agent Factory — i18n Module
 * 한국어/영어 다국어 전환 지원
 */
const I18n = (() => {
    let currentLang = localStorage.getItem('af-lang') || 'ko';
    let translations = {};

    async function load(lang) {
        try {
            const res = await fetch(`/api/i18n/${lang}`);
            translations = await res.json();
            currentLang = lang;
            localStorage.setItem('af-lang', lang);
            document.documentElement.lang = lang;
            document.documentElement.dataset.lang = lang;
            applyAll();
            // 동적 렌더 컴포넌트들에게 언어 변경 알림
            document.dispatchEvent(new CustomEvent('langchange', { detail: { lang } }));
        } catch (e) {
            console.error('[i18n] Load failed:', e);
        }
    }

    function t(key) {
        return translations[key] || key;
    }

    function applyAll() {
        // data-i18n 텍스트
        document.querySelectorAll('[data-i18n]').forEach(el => {
            el.textContent = t(el.dataset.i18n);
        });
        // data-i18n-placeholder
        document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
            el.placeholder = t(el.dataset.i18nPlaceholder);
        });
        // 언어 버튼 상태
        document.querySelectorAll('.lang-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.lang === currentLang);
        });
        // 설정 페이지 라디오
        document.querySelectorAll('input[name="lang"]').forEach(radio => {
            radio.checked = radio.value === currentLang;
        });
    }

    function getLang() { return currentLang; }

    return { load, t, applyAll, getLang };
})();
