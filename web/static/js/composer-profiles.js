/* Dedicated command boundary for managed installation-profile authoring.
 *
 * Composer owns shared page state and rendering.  This module owns every
 * user-facing profile command so the canonical 33 x 138 editor has one
 * explicit integration surface rather than a second direct-frame mode.
 */
(function installComposerProfiles(global) {
    'use strict';

    function required(name, value) {
        if (typeof value !== 'function') {
            throw new Error(`Composer profile integration is missing ${name}.`);
        }
        return value;
    }

    function install(context) {
        const $ = required('$', context.$);
        const openEditor = required('openEditor', context.openEditor);
        const closeEditor = required('closeEditor', context.closeEditor);
        const undo = required('undo', context.undo);
        const revert = required('revert', context.revert);
        const save = required('save', context.save);
        const publish = required('publish', context.publish);
        const review = required('review', context.review);
        const stage = required('stage', context.stage);
        const setZoom = required('setZoom', context.setZoom);
        const setTool = required('setTool', context.setTool);
        const beginStroke = required('beginStroke', context.beginStroke);
        const continueStroke = required('continueStroke', context.continueStroke);
        const endStroke = required('endStroke', context.endStroke);
        const handleKeydown = required('handleKeydown', context.handleKeydown);
        const renderCanvas = required('renderCanvas', context.renderCanvas);

        return Object.freeze({
            bind() {
                $('editMasksButton').addEventListener('click', openEditor);
                $('closeMaskEditorButton').addEventListener('click', closeEditor);
                $('doneMaskEditorButton').addEventListener('click', closeEditor);
                $('undoMaskButton').addEventListener('click', undo);
                $('revertMasksButton').addEventListener('click', revert);
                $('saveMasksButton').addEventListener('click', save);
                $('publishProfileButton').addEventListener('click', publish);
                $('reviewProfileCandidateButton').addEventListener('click', review);
                $('confirmProfileCandidateButton').addEventListener('click', (event) => {
                    event.preventDefault();
                    $('profileCandidateDialog').close();
                    stage();
                });
                $('maskZoom').addEventListener('input', (event) => setZoom(event.target.value));
                document.querySelectorAll('[data-mask-tool]').forEach((button) => {
                    button.addEventListener('click', () => setTool(button.dataset.maskTool));
                });
                $('maskCanvas').addEventListener('pointerdown', beginStroke);
                $('maskCanvas').addEventListener('pointermove', continueStroke);
                $('maskCanvas').addEventListener('pointerup', endStroke);
                $('maskCanvas').addEventListener('pointercancel', endStroke);
                $('maskCanvas').addEventListener('keydown', handleKeydown);
                $('maskCanvas').addEventListener('focus', renderCanvas);
                $('maskCanvas').addEventListener('blur', renderCanvas);
            },
        });
    }

    global.ComposerProfiles = Object.freeze({install});
}(window));
