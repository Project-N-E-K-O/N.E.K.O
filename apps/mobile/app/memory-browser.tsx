import React, { useState, useEffect } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  SafeAreaView,
  ScrollView,
  TextInput,
  Alert,
  FlatList,
} from 'react-native';
import { router } from 'expo-router';

interface MemoryItem {
  id: string;
  content: string;
  timestamp: string;
  type: string;
  relevance: number;
}

export default function MemoryBrowserScreen() {
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedType, setSelectedType] = useState('all');
  const [isLoading, setIsLoading] = useState(false);

  const memoryTypes = [
    { id: 'all', name: '全部' },
    { id: 'conversation', name: '对话' },
    { id: 'fact', name: '事实' },
    { id: 'preference', name: '偏好' },
    { id: 'emotion', name: '情感' },
  ];

  // 模拟数据
  useEffect(() => {
    const mockMemories: MemoryItem[] = [
      {
        id: '1',
        content: '用户喜欢喝咖啡，特别是美式咖啡',
        timestamp: '2024-01-15 10:30',
        type: 'preference',
        relevance: 0.95,
      },
      {
        id: '2',
        content: '用户今天心情不太好，因为工作压力大',
        timestamp: '2024-01-15 14:20',
        type: 'emotion',
        relevance: 0.88,
      },
      {
        id: '3',
        content: '用户询问了关于人工智能的发展历史',
        timestamp: '2024-01-15 16:45',
        type: 'conversation',
        relevance: 0.92,
      },
      {
        id: '4',
        content: '用户住在北京，是一名软件工程师',
        timestamp: '2024-01-14 09:15',
        type: 'fact',
        relevance: 0.98,
      },
    ];
    setMemories(mockMemories);
  }, []);

  const filteredMemories = memories.filter(memory => {
    const matchesSearch = memory.content.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesType = selectedType === 'all' || memory.type === selectedType;
    return matchesSearch && matchesType;
  });

  const deleteMemory = (id: string) => {
    Alert.alert(
      '确认删除',
      '确定要删除这条记忆吗？',
      [
        { text: '取消', style: 'cancel' },
        {
          text: '删除',
          style: 'destructive',
          onPress: () => {
            setMemories(memories.filter(m => m.id !== id));
          },
        },
      ]
    );
  };

  const getTypeColor = (type: string) => {
    switch (type) {
      case 'conversation': return '#007AFF';
      case 'fact': return '#34C759';
      case 'preference': return '#FF9500';
      case 'emotion': return '#FF3B30';
      default: return '#8E8E93';
    }
  };

  const getTypeName = (type: string) => {
    const typeObj = memoryTypes.find(t => t.id === type);
    return typeObj ? typeObj.name : type;
  };

  const renderMemoryItem = ({ item }: { item: MemoryItem }) => (
    <View style={styles.memoryItem}>
      <View style={styles.memoryHeader}>
        <View style={styles.memoryMeta}>
          <Text style={styles.timestamp}>{item.timestamp}</Text>
          <View style={[styles.typeBadge, { backgroundColor: getTypeColor(item.type) }]}>
            <Text style={styles.typeText}>{getTypeName(item.type)}</Text>
          </View>
        </View>
        <View style={styles.memoryActions}>
          <Text style={styles.relevanceText}>
            相关度: {(item.relevance * 100).toFixed(0)}%
          </Text>
          <TouchableOpacity
            style={styles.deleteButton}
            onPress={() => deleteMemory(item.id)}
          >
            <Text style={styles.deleteButtonText}>删除</Text>
          </TouchableOpacity>
        </View>
      </View>
      <Text style={styles.memoryContent}>{item.content}</Text>
    </View>
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
        <Text style={styles.title}>记忆浏览器</Text>
        <TouchableOpacity 
          style={styles.refreshButton}
          onPress={() => setIsLoading(true)}
        >
          <Text style={styles.refreshButtonText}>刷新</Text>
        </TouchableOpacity>
      </View>

      {/* 搜索和筛选 */}
      <View style={styles.searchContainer}>
        <TextInput
          style={styles.searchInput}
          placeholder="搜索记忆内容..."
          value={searchQuery}
          onChangeText={setSearchQuery}
        />
        <ScrollView 
          horizontal 
          showsHorizontalScrollIndicator={false}
          style={styles.filterScroll}
        >
          {memoryTypes.map((type) => (
            <TouchableOpacity
              key={type.id}
              style={[
                styles.filterButton,
                selectedType === type.id && styles.filterButtonActive
              ]}
              onPress={() => setSelectedType(type.id)}
            >
              <Text style={[
                styles.filterButtonText,
                selectedType === type.id && styles.filterButtonTextActive
              ]}>
                {type.name}
              </Text>
            </TouchableOpacity>
          ))}
        </ScrollView>
      </View>

      {/* 统计信息 */}
      <View style={styles.statsContainer}>
        <Text style={styles.statsText}>
          共 {filteredMemories.length} 条记忆
          {searchQuery && ` (搜索: "${searchQuery}")`}
        </Text>
      </View>

      {/* 记忆列表 */}
      <FlatList
        data={filteredMemories}
        renderItem={renderMemoryItem}
        keyExtractor={(item) => item.id}
        style={styles.memoryList}
        showsVerticalScrollIndicator={false}
        ListEmptyComponent={
          <View style={styles.emptyContainer}>
            <Text style={styles.emptyText}>
              {searchQuery ? '没有找到匹配的记忆' : '暂无记忆数据'}
            </Text>
          </View>
        }
      />

      {/* 底部操作 */}
      <View style={styles.bottomActions}>
        <TouchableOpacity 
          style={styles.actionButton}
          onPress={() => Alert.alert('功能', '导出记忆功能')}
        >
          <Text style={styles.actionButtonText}>导出</Text>
        </TouchableOpacity>
        <TouchableOpacity 
          style={styles.actionButton}
          onPress={() => Alert.alert('功能', '清理过期记忆功能')}
        >
          <Text style={styles.actionButtonText}>清理</Text>
        </TouchableOpacity>
        <TouchableOpacity 
          style={styles.actionButton}
          onPress={() => Alert.alert('功能', '记忆分析功能')}
        >
          <Text style={styles.actionButtonText}>分析</Text>
        </TouchableOpacity>
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
  refreshButton: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    backgroundColor: '#007AFF',
    borderRadius: 6,
  },
  refreshButtonText: {
    color: '#fff',
    fontSize: 14,
  },
  searchContainer: {
    padding: 16,
    backgroundColor: '#fff',
    borderBottomWidth: 1,
    borderBottomColor: '#e1e5e9',
  },
  searchInput: {
    borderWidth: 1,
    borderColor: '#e1e5e9',
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
    fontSize: 16,
    marginBottom: 12,
  },
  filterScroll: {
    marginBottom: 8,
  },
  filterButton: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    backgroundColor: '#f0f0f0',
    borderRadius: 16,
    marginRight: 8,
  },
  filterButtonActive: {
    backgroundColor: '#007AFF',
  },
  filterButtonText: {
    color: '#333',
    fontSize: 14,
  },
  filterButtonTextActive: {
    color: '#fff',
  },
  statsContainer: {
    padding: 12,
    backgroundColor: '#fff',
    borderBottomWidth: 1,
    borderBottomColor: '#e1e5e9',
  },
  statsText: {
    color: '#666',
    fontSize: 14,
  },
  memoryList: {
    flex: 1,
  },
  memoryItem: {
    backgroundColor: '#fff',
    margin: 8,
    padding: 16,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#e1e5e9',
  },
  memoryHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    marginBottom: 8,
  },
  memoryMeta: {
    flex: 1,
  },
  timestamp: {
    color: '#666',
    fontSize: 12,
    marginBottom: 4,
  },
  typeBadge: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 10,
    alignSelf: 'flex-start',
  },
  typeText: {
    color: '#fff',
    fontSize: 12,
    fontWeight: '500',
  },
  memoryActions: {
    alignItems: 'flex-end',
  },
  relevanceText: {
    color: '#666',
    fontSize: 12,
    marginBottom: 4,
  },
  deleteButton: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    backgroundColor: '#FF3B30',
    borderRadius: 4,
  },
  deleteButtonText: {
    color: '#fff',
    fontSize: 12,
  },
  memoryContent: {
    fontSize: 16,
    color: '#333',
    lineHeight: 22,
  },
  emptyContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    padding: 40,
  },
  emptyText: {
    color: '#666',
    fontSize: 16,
    textAlign: 'center',
  },
  bottomActions: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    padding: 16,
    backgroundColor: '#fff',
    borderTopWidth: 1,
    borderTopColor: '#e1e5e9',
  },
  actionButton: {
    paddingHorizontal: 20,
    paddingVertical: 10,
    backgroundColor: '#f0f0f0',
    borderRadius: 8,
  },
  actionButtonText: {
    color: '#333',
    fontSize: 14,
    fontWeight: '500',
  },
});
