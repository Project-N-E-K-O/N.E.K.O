import React, { useState } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  SafeAreaView,
  ScrollView,
  Alert,
  FlatList,
  TextInput,
} from 'react-native';
import { router } from 'expo-router';

interface Emotion {
  id: string;
  name: string;
  description: string;
  isActive: boolean;
  duration: number;
  trigger: string;
  category: string;
}

interface Motion {
  id: string;
  name: string;
  description: string;
  isActive: boolean;
  duration: number;
  loop: boolean;
  category: string;
}

export default function Live2DEmotionManagerScreen() {
  const [emotions, setEmotions] = useState<Emotion[]>([
    {
      id: '1',
      name: '开心',
      description: '开心的表情',
      isActive: true,
      duration: 3000,
      trigger: 'happy',
      category: '基础表情',
    },
    {
      id: '2',
      name: '悲伤',
      description: '悲伤的表情',
      isActive: true,
      duration: 3000,
      trigger: 'sad',
      category: '基础表情',
    },
    {
      id: '3',
      name: '生气',
      description: '生气的表情',
      isActive: true,
      duration: 3000,
      trigger: 'angry',
      category: '基础表情',
    },
    {
      id: '4',
      name: '惊讶',
      description: '惊讶的表情',
      isActive: false,
      duration: 2000,
      trigger: 'surprised',
      category: '特殊表情',
    },
  ]);

  const [motions, setMotions] = useState<Motion[]>([
    {
      id: '1',
      name: '挥手',
      description: '挥手动作',
      isActive: true,
      duration: 2000,
      loop: false,
      category: '基础动作',
    },
    {
      id: '2',
      name: '点头',
      description: '点头动作',
      isActive: true,
      duration: 1500,
      loop: false,
      category: '基础动作',
    },
    {
      id: '3',
      name: '跳舞',
      description: '跳舞动作',
      isActive: false,
      duration: 5000,
      loop: true,
      category: '特殊动作',
    },
  ]);

  const [selectedTab, setSelectedTab] = useState<'emotions' | 'motions'>('emotions');
  const [selectedItem, setSelectedItem] = useState<Emotion | Motion | null>(null);
  const [isEditing, setIsEditing] = useState(false);
  const [editName, setEditName] = useState('');
  const [editDescription, setEditDescription] = useState('');
  const [editDuration, setEditDuration] = useState('');

  const toggleItem = (id: string) => {
    if (selectedTab === 'emotions') {
      setEmotions(emotions.map(emotion => 
        emotion.id === id ? { ...emotion, isActive: !emotion.isActive } : emotion
      ));
    } else {
      setMotions(motions.map(motion => 
        motion.id === id ? { ...motion, isActive: !motion.isActive } : motion
      ));
    }
  };

  const deleteItem = (id: string) => {
    Alert.alert(
      '确认删除',
      '确定要删除这个项目吗？',
      [
        { text: '取消', style: 'cancel' },
        {
          text: '删除',
          style: 'destructive',
          onPress: () => {
            if (selectedTab === 'emotions') {
              setEmotions(emotions.filter(item => item.id !== id));
            } else {
              setMotions(motions.filter(item => item.id !== id));
            }
            if (selectedItem?.id === id) {
              setSelectedItem(null);
            }
          },
        },
      ]
    );
  };

  const startEditing = (item: Emotion | Motion) => {
    setSelectedItem(item);
    setEditName(item.name);
    setEditDescription(item.description);
    setEditDuration(item.duration.toString());
    setIsEditing(true);
  };

  const saveEdit = () => {
    if (!selectedItem) return;
    
    const updatedItem = {
      ...selectedItem,
      name: editName,
      description: editDescription,
      duration: parseInt(editDuration) || 3000,
    };

    if (selectedTab === 'emotions') {
      setEmotions(emotions.map(item => 
        item.id === selectedItem.id ? updatedItem as Emotion : item
      ));
    } else {
      setMotions(motions.map(item => 
        item.id === selectedItem.id ? updatedItem as Motion : item
      ));
    }
    
    setIsEditing(false);
    setSelectedItem(null);
    Alert.alert('保存成功', '项目已更新');
  };

  const cancelEdit = () => {
    setIsEditing(false);
    setSelectedItem(null);
  };

  const previewItem = (item: Emotion | Motion) => {
    Alert.alert('预览', `正在预览 ${item.name}...`);
  };

  const createNewItem = () => {
    const newItem = selectedTab === 'emotions' 
      ? {
          id: Date.now().toString(),
          name: '新表情',
          description: '表情描述',
          isActive: false,
          duration: 3000,
          trigger: 'new',
          category: '基础表情',
        } as Emotion
      : {
          id: Date.now().toString(),
          name: '新动作',
          description: '动作描述',
          isActive: false,
          duration: 2000,
          loop: false,
          category: '基础动作',
        } as Motion;

    if (selectedTab === 'emotions') {
      setEmotions([...emotions, newItem as Emotion]);
    } else {
      setMotions([...motions, newItem as Motion]);
    }
    
    setSelectedItem(newItem);
    setEditName(newItem.name);
    setEditDescription(newItem.description);
    setEditDuration(newItem.duration.toString());
    setIsEditing(true);
  };

  const renderEmotionItem = ({ item }: { item: Emotion }) => (
    <TouchableOpacity
      style={[
        styles.itemCard,
        selectedItem?.id === item.id && styles.itemCardSelected
      ]}
      onPress={() => setSelectedItem(item)}
    >
      <View style={styles.itemHeader}>
        <Text style={styles.itemName}>{item.name}</Text>
        <View style={[
          styles.statusBadge,
          { backgroundColor: item.isActive ? '#34C759' : '#8E8E93' }
        ]}>
          <Text style={styles.statusText}>
            {item.isActive ? '启用' : '禁用'}
          </Text>
        </View>
      </View>
      <Text style={styles.itemDesc}>{item.description}</Text>
      <View style={styles.itemMeta}>
        <Text style={styles.itemMetaText}>触发: {item.trigger}</Text>
        <Text style={styles.itemMetaText}>时长: {item.duration}ms</Text>
      </View>
      <View style={styles.itemCategory}>
        <Text style={styles.categoryText}>{item.category}</Text>
      </View>
    </TouchableOpacity>
  );

  const renderMotionItem = ({ item }: { item: Motion }) => (
    <TouchableOpacity
      style={[
        styles.itemCard,
        selectedItem?.id === item.id && styles.itemCardSelected
      ]}
      onPress={() => setSelectedItem(item)}
    >
      <View style={styles.itemHeader}>
        <Text style={styles.itemName}>{item.name}</Text>
        <View style={[
          styles.statusBadge,
          { backgroundColor: item.isActive ? '#34C759' : '#8E8E93' }
        ]}>
          <Text style={styles.statusText}>
            {item.isActive ? '启用' : '禁用'}
          </Text>
        </View>
      </View>
      <Text style={styles.itemDesc}>{item.description}</Text>
      <View style={styles.itemMeta}>
        <Text style={styles.itemMetaText}>循环: {item.loop ? '是' : '否'}</Text>
        <Text style={styles.itemMetaText}>时长: {item.duration}ms</Text>
      </View>
      <View style={styles.itemCategory}>
        <Text style={styles.categoryText}>{item.category}</Text>
      </View>
    </TouchableOpacity>
  );

  return (
    <SafeAreaView style={styles.container}>
      {/* 顶部导航 */}
      <View style={styles.header}>
        <TouchableOpacity 
          style={styles.backButton}
          onPress={() => router.back()}
        >
          <Text style={styles.backButtonText}>返回</Text>
        </TouchableOpacity>
        <Text style={styles.title}>表情管理</Text>
        <TouchableOpacity 
          style={styles.newButton}
          onPress={createNewItem}
        >
          <Text style={styles.newButtonText}>新建</Text>
        </TouchableOpacity>
      </View>

      {/* 标签切换 */}
      <View style={styles.tabContainer}>
        <TouchableOpacity
          style={[
            styles.tabButton,
            selectedTab === 'emotions' && styles.tabButtonActive
          ]}
          onPress={() => setSelectedTab('emotions')}
        >
          <Text style={[
            styles.tabButtonText,
            selectedTab === 'emotions' && styles.tabButtonTextActive
          ]}>
            表情 ({emotions.length})
          </Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[
            styles.tabButton,
            selectedTab === 'motions' && styles.tabButtonActive
          ]}
          onPress={() => setSelectedTab('motions')}
        >
          <Text style={[
            styles.tabButtonText,
            selectedTab === 'motions' && styles.tabButtonTextActive
          ]}>
            动作 ({motions.length})
          </Text>
        </TouchableOpacity>
      </View>

      <View style={styles.content}>
        {/* 列表区域 */}
        <View style={styles.listSection}>
          <FlatList
            data={selectedTab === 'emotions' ? emotions : motions}
            renderItem={selectedTab === 'emotions' ? renderEmotionItem : renderMotionItem}
            keyExtractor={(item) => item.id}
            style={styles.itemList}
            showsVerticalScrollIndicator={false}
          />
        </View>

        {/* 详情区域 */}
        {selectedItem && (
          <View style={styles.detailSection}>
            <Text style={styles.sectionTitle}>详情</Text>
            
            {isEditing ? (
              <View style={styles.editForm}>
                <Text style={styles.formLabel}>名称</Text>
                <TextInput
                  style={styles.formInput}
                  value={editName}
                  onChangeText={setEditName}
                  placeholder="输入名称"
                />
                
                <Text style={styles.formLabel}>描述</Text>
                <TextInput
                  style={styles.formInput}
                  value={editDescription}
                  onChangeText={setEditDescription}
                  placeholder="输入描述"
                  multiline
                />
                
                <Text style={styles.formLabel}>时长 (毫秒)</Text>
                <TextInput
                  style={styles.formInput}
                  value={editDuration}
                  onChangeText={setEditDuration}
                  placeholder="3000"
                  keyboardType="numeric"
                />
                
                <View style={styles.editButtons}>
                  <TouchableOpacity 
                    style={styles.saveButton}
                    onPress={saveEdit}
                  >
                    <Text style={styles.saveButtonText}>保存</Text>
                  </TouchableOpacity>
                  <TouchableOpacity 
                    style={styles.cancelButton}
                    onPress={cancelEdit}
                  >
                    <Text style={styles.cancelButtonText}>取消</Text>
                  </TouchableOpacity>
                </View>
              </View>
            ) : (
              <View style={styles.itemDetail}>
                <Text style={styles.detailName}>{selectedItem.name}</Text>
                <Text style={styles.detailDesc}>{selectedItem.description}</Text>
                
                <View style={styles.detailInfo}>
                  <View style={styles.infoRow}>
                    <Text style={styles.infoLabel}>状态:</Text>
                    <Text style={styles.infoValue}>
                      {selectedItem.isActive ? '启用' : '禁用'}
                    </Text>
                  </View>
                  <View style={styles.infoRow}>
                    <Text style={styles.infoLabel}>时长:</Text>
                    <Text style={styles.infoValue}>{selectedItem.duration}ms</Text>
                  </View>
                  {selectedTab === 'emotions' && (
                    <View style={styles.infoRow}>
                      <Text style={styles.infoLabel}>触发:</Text>
                      <Text style={styles.infoValue}>{(selectedItem as Emotion).trigger}</Text>
                    </View>
                  )}
                  {selectedTab === 'motions' && (
                    <View style={styles.infoRow}>
                      <Text style={styles.infoLabel}>循环:</Text>
                      <Text style={styles.infoValue}>
                        {(selectedItem as Motion).loop ? '是' : '否'}
                      </Text>
                    </View>
                  )}
                </View>
                
                <View style={styles.actionButtons}>
                  <TouchableOpacity 
                    style={styles.actionButton}
                    onPress={() => previewItem(selectedItem)}
                  >
                    <Text style={styles.actionButtonText}>预览</Text>
                  </TouchableOpacity>
                  
                  <TouchableOpacity 
                    style={[styles.actionButton, styles.toggleButton]}
                    onPress={() => toggleItem(selectedItem.id)}
                  >
                    <Text style={styles.actionButtonText}>
                      {selectedItem.isActive ? '禁用' : '启用'}
                    </Text>
                  </TouchableOpacity>
                  
                  <TouchableOpacity 
                    style={styles.actionButton}
                    onPress={() => startEditing(selectedItem)}
                  >
                    <Text style={styles.actionButtonText}>编辑</Text>
                  </TouchableOpacity>
                  
                  <TouchableOpacity 
                    style={[styles.actionButton, styles.deleteButton]}
                    onPress={() => deleteItem(selectedItem.id)}
                  >
                    <Text style={styles.actionButtonText}>删除</Text>
                  </TouchableOpacity>
                </View>
              </View>
            )}
          </View>
        )}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#f7f8fa',
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 16,
    backgroundColor: '#fff',
    borderBottomWidth: 1,
    borderBottomColor: '#e1e5e9',
  },
  backButton: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    backgroundColor: '#f0f0f0',
    borderRadius: 6,
  },
  backButtonText: {
    color: '#333',
    fontSize: 14,
  },
  title: {
    fontSize: 18,
    fontWeight: 'bold',
    color: '#333',
  },
  newButton: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    backgroundColor: '#007AFF',
    borderRadius: 6,
  },
  newButtonText: {
    color: '#fff',
    fontSize: 14,
  },
  tabContainer: {
    flexDirection: 'row',
    backgroundColor: '#fff',
    paddingHorizontal: 16,
    paddingVertical: 8,
  },
  tabButton: {
    flex: 1,
    paddingVertical: 8,
    alignItems: 'center',
    borderRadius: 6,
    marginHorizontal: 4,
  },
  tabButtonActive: {
    backgroundColor: '#007AFF',
  },
  tabButtonText: {
    fontSize: 14,
    color: '#666',
  },
  tabButtonTextActive: {
    color: '#fff',
    fontWeight: '500',
  },
  content: {
    flex: 1,
    flexDirection: 'row',
  },
  listSection: {
    flex: 1,
    padding: 16,
  },
  detailSection: {
    flex: 1,
    padding: 16,
    backgroundColor: '#fff',
    margin: 8,
    borderRadius: 12,
  },
  sectionTitle: {
    fontSize: 16,
    fontWeight: 'bold',
    color: '#333',
    marginBottom: 16,
  },
  itemList: {
    flex: 1,
  },
  itemCard: {
    backgroundColor: '#fff',
    padding: 16,
    borderRadius: 12,
    marginBottom: 8,
    borderWidth: 1,
    borderColor: '#e1e5e9',
  },
  itemCardSelected: {
    borderColor: '#007AFF',
    backgroundColor: '#f0f8ff',
  },
  itemHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 8,
  },
  itemName: {
    fontSize: 16,
    fontWeight: 'bold',
    color: '#333',
  },
  statusBadge: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 12,
  },
  statusText: {
    color: '#fff',
    fontSize: 12,
    fontWeight: '500',
  },
  itemDesc: {
    fontSize: 14,
    color: '#666',
    marginBottom: 8,
  },
  itemMeta: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: 8,
  },
  itemMetaText: {
    fontSize: 12,
    color: '#999',
  },
  itemCategory: {
    alignSelf: 'flex-start',
  },
  categoryText: {
    fontSize: 12,
    color: '#007AFF',
    backgroundColor: '#f0f8ff',
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 8,
  },
  itemDetail: {
    flex: 1,
  },
  detailName: {
    fontSize: 20,
    fontWeight: 'bold',
    color: '#333',
    marginBottom: 12,
  },
  detailDesc: {
    fontSize: 16,
    color: '#666',
    marginBottom: 20,
    lineHeight: 22,
  },
  detailInfo: {
    marginBottom: 20,
  },
  infoRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: '#f0f0f0',
  },
  infoLabel: {
    fontSize: 14,
    color: '#666',
  },
  infoValue: {
    fontSize: 14,
    color: '#333',
    fontWeight: '500',
  },
  actionButtons: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    flexWrap: 'wrap',
    gap: 8,
  },
  actionButton: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    backgroundColor: '#f0f0f0',
    borderRadius: 6,
    minWidth: 60,
    alignItems: 'center',
  },
  toggleButton: {
    backgroundColor: '#34C759',
  },
  deleteButton: {
    backgroundColor: '#FF3B30',
  },
  actionButtonText: {
    color: '#333',
    fontSize: 12,
    fontWeight: '500',
  },
  editForm: {
    flex: 1,
  },
  formLabel: {
    fontSize: 16,
    fontWeight: '500',
    color: '#333',
    marginBottom: 8,
  },
  formInput: {
    borderWidth: 1,
    borderColor: '#e1e5e9',
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
    fontSize: 16,
    marginBottom: 16,
  },
  editButtons: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    marginTop: 20,
  },
  saveButton: {
    paddingHorizontal: 20,
    paddingVertical: 10,
    backgroundColor: '#007AFF',
    borderRadius: 8,
  },
  saveButtonText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '500',
  },
  cancelButton: {
    paddingHorizontal: 20,
    paddingVertical: 10,
    backgroundColor: '#f0f0f0',
    borderRadius: 8,
  },
  cancelButtonText: {
    color: '#333',
    fontSize: 16,
    fontWeight: '500',
  },
});
