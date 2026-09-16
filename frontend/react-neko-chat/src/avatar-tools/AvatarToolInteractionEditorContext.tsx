import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useReducer,
  useState,
  type Dispatch,
  type ReactNode,
} from 'react';
import {
  avatarToolInteractionEditorReducer,
  createAvatarToolInteractionEditorState,
  type AvatarToolInteractionEditorAction,
  type AvatarToolInteractionEditorState,
  type AvatarToolInteractionValidationIssue,
} from './avatarToolInteractionEditorModel';
import type { AvatarToolImageDraft, AvatarToolImageId } from './avatarToolEditorModel';

type AvatarToolInteractionEditorContextValue = {
  state: AvatarToolInteractionEditorState;
  dispatch: Dispatch<AvatarToolInteractionEditorAction>;
  issues: AvatarToolInteractionValidationIssue[];
  setIssues(issues: AvatarToolInteractionValidationIssue[]): void;
  images: AvatarToolImageDraft[];
  initialImageId: AvatarToolImageId | null;
  setImageState(images: AvatarToolImageDraft[], initialImageId: AvatarToolImageId | null): void;
  graphRevision: number;
};

const AvatarToolInteractionEditorContext = createContext<AvatarToolInteractionEditorContextValue | null>(null);

function actionChangesGraphValidation(action: AvatarToolInteractionEditorAction): boolean {
  return action.type === 'add'
    || action.type === 'update-name'
    || action.type === 'update-click-action'
    || action.type === 'update-delay'
    || action.type === 'update-delay-action'
    || action.type === 'connect-initial-image'
    || action.type === 'remove-initial-link'
    || action.type === 'connect'
    || action.type === 'remove-link'
    || action.type === 'remove-interaction'
    || action.type === 'duplicate-interaction';
}

export function AvatarToolInteractionEditorProvider({ children }: { children: ReactNode }) {
  const [state, baseDispatch] = useReducer(
    avatarToolInteractionEditorReducer,
    undefined,
    () => createAvatarToolInteractionEditorState(),
  );
  const [issues, setIssues] = useState<AvatarToolInteractionValidationIssue[]>([]);
  const [imageState, setImageStateValue] = useState<{
    images: AvatarToolImageDraft[];
    initialImageId: AvatarToolImageId | null;
  }>({ images: [], initialImageId: null });
  const [graphRevision, setGraphRevision] = useState(0);
  const dispatch = useCallback<Dispatch<AvatarToolInteractionEditorAction>>((action) => {
    if (action.type === 'reset') {
      setIssues([]);
      setGraphRevision(revision => revision + 1);
    } else if (actionChangesGraphValidation(action)) {
      setGraphRevision(revision => revision + 1);
    }
    baseDispatch(action);
  }, []);
  const setImageState = useCallback((
    images: AvatarToolImageDraft[],
    initialImageId: AvatarToolImageId | null,
  ) => {
    setImageStateValue({ images, initialImageId });
  }, []);
  const value = useMemo(
    () => ({
      state,
      dispatch,
      issues,
      setIssues,
      images: imageState.images,
      initialImageId: imageState.initialImageId,
      setImageState,
      graphRevision,
    }),
    [dispatch, graphRevision, imageState, issues, setImageState, state],
  );

  return (
    <AvatarToolInteractionEditorContext.Provider value={value}>
      {children}
    </AvatarToolInteractionEditorContext.Provider>
  );
}

export function useAvatarToolInteractionEditor(): AvatarToolInteractionEditorContextValue {
  const value = useContext(AvatarToolInteractionEditorContext);
  if (!value) throw new Error('AvatarToolInteractionEditorProvider is required');
  return value;
}
