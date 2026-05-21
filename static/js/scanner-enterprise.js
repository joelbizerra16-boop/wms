/**
 * Modo scanner enterprise híbrido (scanner físico + digitação manual).
 * Uso: WMSScannerEnterprise.create({ input, form, modulo, isReady, onProcessar, setScannerStatus })
 */
(function (global) {
    'use strict';

    var FOCUS_INTERVAL_MS = 800;
    var MANUAL_IDLE_MS = 5000;
    var SCANNER_BUFFER_MS = 120;
    var LOG_THROTTLE_MS = 2000;

    function logOperacional(modulo, evento, extra) {
        var msg = '[WMS_SCANNER] ' + evento + ' modulo=' + (modulo || 'operacional');
        if (extra) msg += ' ' + extra;
        if (global.console && global.console.info) {
            global.console.info(msg);
        }
    }

    function isEditableElement(element, input) {
        if (!element || element === input) return false;
        var tagName = (element.tagName || '').toUpperCase();
        return element.isContentEditable || tagName === 'INPUT' || tagName === 'TEXTAREA' || tagName === 'SELECT';
    }

    function create(config) {
        var input = config.input;
        var form = config.form || null;
        var modulo = config.modulo || 'operacional';
        var isReady = config.isReady || function () { return Boolean(input && !input.disabled); };
        var onProcessar = config.onProcessar || function () {};
        var setScannerStatus = config.setScannerStatus || function () {};
        var focusIntervalMs = config.focusIntervalMs || FOCUS_INTERVAL_MS;
        var manualIdleMs = config.manualIdleMs || MANUAL_IDLE_MS;
        var scannerTimeoutMs = config.scannerTimeoutMs || SCANNER_BUFFER_MS;

        var scannerModeAtivo = true;
        var digitacaoManualAtiva = false;
        var focoProgramatico = false;
        var scannerBuffer = '';
        var scannerTimer = null;
        var manualIdleTimer = null;
        var focusTimer = null;
        var focusIntervalHandle = null;
        var lastFocusLogAt = 0;
        var destruido = false;

        function logThrottled(evento, extra) {
            var agora = Date.now();
            if (evento === 'SCANNER_FOCUS_RESTORED') {
                if (agora - lastFocusLogAt < LOG_THROTTLE_MS) return;
                lastFocusLogAt = agora;
            }
            logOperacional(modulo, evento, extra);
        }

        function clearScannerBuffer() {
            scannerBuffer = '';
            if (scannerTimer) {
                clearTimeout(scannerTimer);
                scannerTimer = null;
            }
        }

        function scheduleScannerTimeout() {
            if (scannerTimer) clearTimeout(scannerTimer);
            scannerTimer = setTimeout(clearScannerBuffer, scannerTimeoutMs);
        }

        function clearManualIdleTimer() {
            if (manualIdleTimer) {
                clearTimeout(manualIdleTimer);
                manualIdleTimer = null;
            }
        }

        function scheduleRetornoScanner() {
            clearManualIdleTimer();
            manualIdleTimer = setTimeout(function () {
                if (destruido || !isReady()) return;
                ativarScannerMode(true);
            }, manualIdleMs);
        }

        function aplicarAtributosInput() {
            if (!input) return;
            input.setAttribute('autocomplete', 'off');
            input.setAttribute('autocorrect', 'off');
            input.setAttribute('autocapitalize', 'off');
            input.setAttribute('spellcheck', 'false');
            input.classList.add('scanner-input');
            if (scannerModeAtivo && !digitacaoManualAtiva) {
                input.setAttribute('inputmode', 'none');
                input.inputMode = 'none';
                input.removeAttribute('readonly');
                input.enterKeyHint = 'done';
            } else {
                input.setAttribute('inputmode', 'text');
                input.inputMode = 'text';
                input.removeAttribute('readonly');
                input.enterKeyHint = 'done';
            }
        }

        function ativarScannerMode(silencioso) {
            scannerModeAtivo = true;
            digitacaoManualAtiva = false;
            clearManualIdleTimer();
            aplicarAtributosInput();
            if (!silencioso) {
                logThrottled('SCANNER_MODE_ACTIVE');
                logThrottled('KEYBOARD_BLOCKED');
            }
            if (isReady()) {
                setScannerStatus('scanner');
            }
            iniciarFocusManager();
        }

        function ativarModoDigitacao(origem) {
            if (!isReady() || digitacaoManualAtiva) {
                if (!digitacaoManualAtiva) {
                    digitacaoManualAtiva = true;
                    scannerModeAtivo = false;
                    aplicarAtributosInput();
                }
                scheduleRetornoScanner();
                return;
            }
            scannerModeAtivo = false;
            digitacaoManualAtiva = true;
            aplicarAtributosInput();
            logThrottled('MANUAL_INPUT_MODE_ACTIVE', 'origem=' + (origem || 'touch'));
            logThrottled('KEYBOARD_RELEASED');
            setScannerStatus('manual');
            scheduleRetornoScanner();
        }

        function focusInputProgramatico() {
            if (!input || !isReady() || digitacaoManualAtiva || destruido) return;
            focoProgramatico = true;
            try {
                input.focus({ preventScroll: true });
            } catch (e) {
                input.focus();
            }
            global.setTimeout(function () {
                focoProgramatico = false;
            }, 0);
        }

        function scannerFocusManagerTick() {
            if (destruido || !isReady() || document.hidden) return;
            if (!scannerModeAtivo || digitacaoManualAtiva) return;
            var ativo = document.activeElement;
            if (ativo === input) return;
            if (isEditableElement(ativo, input)) return;
            focusInputProgramatico();
            logThrottled('SCANNER_FOCUS_RESTORED');
        }

        function iniciarFocusManager() {
            if (focusIntervalHandle || destruido) return;
            focusIntervalHandle = global.setInterval(scannerFocusManagerTick, focusIntervalMs);
        }

        function pararFocusManager() {
            if (focusIntervalHandle) {
                global.clearInterval(focusIntervalHandle);
                focusIntervalHandle = null;
            }
        }

        function scheduleInputFocus() {
            if (!isReady() || document.hidden || digitacaoManualAtiva || destruido) return;
            if (focusTimer) clearTimeout(focusTimer);
            focusTimer = global.setTimeout(function () {
                focusTimer = null;
                if (!scannerModeAtivo || digitacaoManualAtiva) return;
                focusInputProgramatico();
            }, 100);
        }

        function onBipagemConcluida() {
            if (input) input.value = '';
            clearScannerBuffer();
            ativarScannerMode(true);
            scheduleInputFocus();
        }

        function onGlobalKeydown(event) {
            if (destruido || !isReady()) return;
            if (event.ctrlKey || event.altKey || event.metaKey) return;

            var ativo = document.activeElement;
            var manualNoCampo = ativo === input;

            if (digitacaoManualAtiva && manualNoCampo) {
                scheduleRetornoScanner();
                if (event.key === 'Enter') {
                    event.preventDefault();
                    var valorManual = (input.value || '').trim();
                    if (valorManual) onProcessar(valorManual, 'manual');
                }
                return;
            }

            if (isEditableElement(ativo, input)) return;

            if (event.key === 'Enter') {
                if (manualNoCampo && digitacaoManualAtiva) return;
                if (!scannerBuffer) return;
                event.preventDefault();
                var codigoLido = scannerBuffer;
                clearScannerBuffer();
                onProcessar(codigoLido, 'scanner');
                return;
            }

            if (manualNoCampo) return;
            if (event.key.length !== 1) return;

            scannerBuffer += event.key;
            scheduleScannerTimeout();
            if (!digitacaoManualAtiva) {
                event.preventDefault();
            }
        }

        function onInputPointerDown() {
            if (!isReady()) return;
            ativarModoDigitacao('pointer');
        }

        function onInputFocus() {
            if (focoProgramatico && scannerModeAtivo) {
                return;
            }
            if (!digitacaoManualAtiva) {
                ativarModoDigitacao('focus');
            } else {
                scheduleRetornoScanner();
            }
        }

        function onInputBlur() {
            if (!isReady() || digitacaoManualAtiva || destruido) return;
            scheduleInputFocus();
        }

        function onInputInput() {
            if (digitacaoManualAtiva) {
                scheduleRetornoScanner();
            }
        }

        function onInputKeydown(event) {
            if (event.key !== 'Enter' || !isReady()) return;
            var valor = (input.value || '').trim();
            if (!valor) return;
            event.preventDefault();
            clearScannerBuffer();
            if (digitacaoManualAtiva) {
                onProcessar(valor, 'manual');
            } else {
                onProcessar(valor, 'scanner');
            }
        }

        function onFormSubmit(event) {
            if (!form) return;
            event.preventDefault();
            ativarModoDigitacao('submit');
            var valor = (input.value || '').trim();
            onProcessar(valor, 'manual');
        }

        function onVisibilityChange() {
            if (document.hidden) {
                pararFocusManager();
                return;
            }
            if (scannerModeAtivo && !digitacaoManualAtiva) {
                iniciarFocusManager();
                scheduleInputFocus();
            }
        }

        function bind() {
            if (!input) return;
            aplicarAtributosInput();
            input.addEventListener('pointerdown', onInputPointerDown);
            input.addEventListener('touchstart', onInputPointerDown, { passive: true });
            input.addEventListener('mousedown', onInputPointerDown);
            input.addEventListener('focus', onInputFocus);
            input.addEventListener('blur', onInputBlur);
            input.addEventListener('input', onInputInput);
            input.addEventListener('keydown', onInputKeydown);
            document.addEventListener('keydown', onGlobalKeydown);
            document.addEventListener('visibilitychange', onVisibilityChange);
            if (form) {
                form.addEventListener('submit', onFormSubmit);
            }
            ativarScannerMode(true);
        }

        function destroy() {
            destruido = true;
            pararFocusManager();
            clearManualIdleTimer();
            clearScannerBuffer();
            if (focusTimer) clearTimeout(focusTimer);
            document.removeEventListener('keydown', onGlobalKeydown);
            document.removeEventListener('visibilitychange', onVisibilityChange);
            if (input) {
                input.removeEventListener('pointerdown', onInputPointerDown);
                input.removeEventListener('touchstart', onInputPointerDown);
                input.removeEventListener('mousedown', onInputPointerDown);
                input.removeEventListener('focus', onInputFocus);
                input.removeEventListener('blur', onInputBlur);
                input.removeEventListener('input', onInputInput);
                input.removeEventListener('keydown', onInputKeydown);
            }
            if (form) {
                form.removeEventListener('submit', onFormSubmit);
            }
        }

        bind();

        return {
            ativarScannerMode: ativarScannerMode,
            ativarModoDigitacao: ativarModoDigitacao,
            scheduleInputFocus: scheduleInputFocus,
            onBipagemConcluida: onBipagemConcluida,
            destroy: destroy,
            isManualMode: function () { return digitacaoManualAtiva; },
            clearBuffer: clearScannerBuffer,
        };
    }

    global.WMSScannerEnterprise = {
        create: create,
    };
})(typeof window !== 'undefined' ? window : this);
