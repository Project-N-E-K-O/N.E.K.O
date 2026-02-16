# Fix Patterns

Examples for fixing hardcoded Chinese text with i18n markers.

## HTML Fixes

### Element Text

```html
<!-- Before -->
<span>发送</span>
<button>确定</button>

<!-- After -->
<span data-i18n="chat.send">发送</span>
<button data-i18n="common.ok">确定</button>
```

### Placeholder

```html
<!-- Before -->
<input placeholder="请输入">

<!-- After -->
<input placeholder="请输入" data-i18n-placeholder="chat.inputPlaceholder">
```

### Title Attribute

```html
<!-- Before -->
<button title="关闭">X</button>

<!-- After -->
<button title="关闭" data-i18n-title="common.close">X</button>
```

### Alt Attribute

```html
<!-- Before -->
<img alt="对话" src="chat.png">

<!-- After -->
<img alt="对话" src="chat.png" data-i18n-alt="chat.title">
```

## JavaScript Fixes

### Display Text

```javascript
// Before
showStatusToast('连接成功', 2000);
element.textContent = '加载中...';

// After
showStatusToast(window.t ? window.t('common.connectionSuccess') : '连接成功', 2000);
element.textContent = window.t ? window.t('common.loading') : '加载中...';
```

### Error Messages

```javascript
// Before
throw new Error('情感分析超时');
showMessage('保存失败', 'error');

// After
throw new Error(window.t ? window.t('error.emotionTimeout') : '情感分析超时');
showMessage(window.t ? window.t('common.saveFailed') : '保存失败', 'error');
```

### With Parameters

```javascript
// Before
showMessage(`删除了 ${count} 个文件`);

// After
showMessage(window.t ? window.t('files.deleted', { count }) : `删除了 ${count} 个文件`);
```

### Placeholder Assignment

```javascript
// Before
input.placeholder = '请输入名称';

// After
input.placeholder = window.t ? window.t('common.enterName') : '请输入名称';
```

## Skip These (Don't Fix)

```javascript
// Console debug messages
console.log('连接成功');
console.error('加载失败:', error);

// Internal logic detection
if (status.includes('已离开')) { ... }
if (data.error.includes('已上传')) { ... }

// Data keys
const name = characterData['档案名'];
const value = data['描述'];

// Already wrapped with fallback
showMessage(window.t ? window.t('key') : 'fallback');
```
