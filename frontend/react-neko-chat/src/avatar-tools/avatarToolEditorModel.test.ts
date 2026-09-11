import {
  avatarToolImageEditorReducer,
  createAvatarToolImageEditorState,
  getAvatarToolImageRemovalBlock,
  type AvatarToolImageDraft,
  type AvatarToolImageId,
} from './avatarToolEditorModel';
import type { LocalAvatarToolDetail } from './localTools';

const DETAIL: LocalAvatarToolDetail = {
  id: 'local-12345678-1234-4123-8123-123456789abc',
  revision: '2-100',
  name: 'Loop',
  changeMode: 'click-advance',
  defaultImage: { resource: 'default.png', url: '/default.png' },
  changeItems: [
    { resource: 'change-000.png', url: '/change-000.png', meaning: 'A' },
    { resource: 'change-001.png', url: '/change-001.png', meaning: '' },
  ],
};

function draft(id: AvatarToolImageId, name: string): AvatarToolImageDraft {
  return {
    id,
    image: new File(['image'], name, { type: 'image/png' }),
    meaning: '',
  };
}

describe('avatar tool image editor model', () => {
  it('reopens v3 images with their stable ids, names, resources, meanings, and initial choice', () => {
    const state = createAvatarToolImageEditorState({
      recordVersion: 3,
      id: DETAIL.id,
      revision: '3-100',
      name: 'Flow',
      images: [
        { id: 'img-idle', name: 'Idle', resource: 'image-000.png', url: '/idle.png', meaning: '' },
        { id: 'img-wave', name: 'Wave', resource: 'image-001.png', url: '/wave.png', meaning: 'waves' },
      ],
      initialImageId: 'img-wave',
      imageInteractions: {
        initialImagePosition: { x: 0, y: 0 },
        initialLinks: [{ to: 'ix-click', sourceSide: 'right', targetSide: 'left' }],
        items: [{
          id: 'ix-click', name: '', trigger: { kind: 'mouse-click' },
          actions: { press: { kind: 'keep' }, release: { kind: 'keep' } },
          editorPosition: { x: 200, y: 0 },
        }],
        links: [{ from: 'ix-click', to: 'ix-click', sourceSide: 'right', targetSide: 'right' }],
      },
    });

    expect(state.initialImageId).toBe('img-wave');
    expect(state.selectedImageId).toBe('img-wave');
    expect(state.images).toEqual([
      { id: 'img-idle', name: 'Idle', image: null, imageResource: 'image-000.png', imageUrl: '/idle.png', meaning: '' },
      { id: 'img-wave', name: 'Wave', image: null, imageResource: 'image-001.png', imageUrl: '/wave.png', meaning: 'waves' },
    ]);
  });

  it('projects v2 resources into deterministic peer image IDs', () => {
    const first = createAvatarToolImageEditorState(DETAIL);
    const second = createAvatarToolImageEditorState(DETAIL);

    expect(first).toEqual(second);
    expect(first.images.map(image => image.id)).toEqual([
      'img-v2-default',
      'img-v2-change-000',
      'img-v2-change-001',
    ]);
    expect(first.images.map(image => image.meaning)).toEqual(['', 'A', '']);
    expect(first.initialImageId).toBe('img-v2-default');
    expect(first.selectedImageId).toBe('img-v2-default');
  });

  it('keeps one valid initial image and enforces the current image limit', () => {
    const imageA = draft('img-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'A.png');
    const imageB = draft('img-bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'B.png');
    let state = createAvatarToolImageEditorState();

    state = avatarToolImageEditorReducer(state, { type: 'add', image: imageA, maximumImages: 1 });
    expect(state.initialImageId).toBe(imageA.id);
    expect(state.selectedImageId).toBe(imageA.id);

    const atLimit = avatarToolImageEditorReducer(state, { type: 'add', image: imageB, maximumImages: 1 });
    expect(atLimit).toBe(state);
    expect(atLimit.images).toHaveLength(1);

    const unknown = avatarToolImageEditorReducer(state, { type: 'choose-initial', imageId: imageB.id });
    expect(unknown).toBe(state);
  });

  it('preserves a stable ID and description when replacing the selected file', () => {
    const initial = createAvatarToolImageEditorState(DETAIL);
    const imageId = initial.images[1].id;
    const replacement = new File(['replacement'], 'replacement.png', { type: 'image/png' });
    const next = avatarToolImageEditorReducer(initial, { type: 'replace', imageId, file: replacement });

    expect(next.images[1]).toMatchObject({ id: imageId, image: replacement, meaning: 'A' });
    expect(next.images[1].imageResource).toBeUndefined();
    expect(next.images[1].imageUrl).toBeUndefined();
    expect(next.selectedImageId).toBe(imageId);
  });

  it('renames an image without changing its stable id or file', () => {
    const initial = createAvatarToolImageEditorState(DETAIL);
    const image = initial.images[1];
    const next = avatarToolImageEditorReducer(initial, {
      type: 'update-name',
      imageId: image.id,
      name: 'Open palm',
    });

    expect(next.images[1]).toMatchObject({
      id: image.id,
      name: 'Open palm',
      imageResource: image.imageResource,
    });
    expect(next.images[0]).toBe(initial.images[0]);
  });

  it('blocks removal through one domain decision and keeps selection valid after removal', () => {
    let state = createAvatarToolImageEditorState(DETAIL);
    const initialId = state.images[0].id;
    const middleId = state.images[1].id;
    const finalId = state.images[2].id;
    const references = { [middleId]: ['鼠标点击 1 · 松开时'] };

    expect(getAvatarToolImageRemovalBlock(state, initialId, references)).toEqual({ kind: 'initial' });
    expect(getAvatarToolImageRemovalBlock(state, middleId, references)).toEqual({
      kind: 'referenced',
      locations: ['鼠标点击 1 · 松开时'],
    });
    expect(getAvatarToolImageRemovalBlock(state, finalId, references)).toBeNull();

    state = avatarToolImageEditorReducer(state, { type: 'select', imageId: middleId });
    state = avatarToolImageEditorReducer(state, { type: 'remove', imageId: middleId });
    expect(state.images.map(image => image.id)).toEqual([initialId, finalId]);
    expect(state.selectedImageId).toBe(finalId);

    const refused = avatarToolImageEditorReducer(state, { type: 'remove', imageId: initialId });
    expect(refused).toBe(state);
  });
});
