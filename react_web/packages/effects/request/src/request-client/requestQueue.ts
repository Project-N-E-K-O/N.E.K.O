import type { AxiosRequestConfig } from 'axios';
import type { QueuedRequest } from './types';

/**
 * 请求队列管理器
 * 用于在 token 刷新期间暂存请求，防止并发刷新
 */
export class RequestQueue {
    private queue: QueuedRequest[] = [];
    private isRefreshing = false;
    private refreshPromise: Promise<void> | null = null;

    /**
     * 添加请求到队列
     */
    enqueue(request: QueuedRequest): void {
        this.queue.push(request);
    }

    /**
     * 处理队列中的所有请求
     */
    async processQueue(error: any = null): Promise<void> {
        const requests = [...this.queue];
        this.queue = [];

        requests.forEach(({ resolve, reject, config }) => {
            if (error) {
                reject(error);
            } else {
                resolve(config);
            }
        });
    }

    /**
     * 开始刷新 token
     */
    startRefresh(): Promise<void> {
        if (this.isRefreshing && this.refreshPromise) {
            return this.refreshPromise;
        }

        this.isRefreshing = true;
        this.refreshPromise = new Promise((resolve, reject) => {
            // 这个 promise 会在 finishRefresh 时 resolve
            this.resolveRefresh = resolve;
            this.rejectRefresh = reject;
        });

        return this.refreshPromise;
    }

    private resolveRefresh?: () => void;
    private rejectRefresh?: (error: any) => void;

    /**
     * 完成刷新（成功）
     */
    async finishRefresh(): Promise<void> {
        this.isRefreshing = false;
        this.refreshPromise = null;
        if (this.resolveRefresh) {
            this.resolveRefresh();
            this.resolveRefresh = undefined;
        }
        await this.processQueue();
    }

    /**
     * 完成刷新（失败）
     */
    async finishRefreshWithError(error: any): Promise<void> {
        this.isRefreshing = false;
        this.refreshPromise = null;
        if (this.rejectRefresh) {
            this.rejectRefresh(error);
            this.rejectRefresh = undefined;
        }
        await this.processQueue(error);
    }

    /**
     * 检查是否正在刷新
     */
    getIsRefreshing(): boolean {
        return this.isRefreshing;
    }

    /**
     * 清空队列
     */
    clear(): void {
        this.queue = [];
        this.isRefreshing = false;
        this.refreshPromise = null;
    }
}

