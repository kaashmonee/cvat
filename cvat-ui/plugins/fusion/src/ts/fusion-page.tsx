// Copyright (C) CVAT.ai Corporation
//
// SPDX-License-Identifier: MIT

import React, {
    useState, useEffect, useCallback, useMemo,
} from 'react';
import { RouteComponentProps, useLocation } from 'react-router-dom';
import Layout from 'antd/lib/layout';
import Spin from 'antd/lib/spin';
import Typography from 'antd/lib/typography';
import Row from 'antd/lib/row';
import Col from 'antd/lib/col';
import Alert from 'antd/lib/alert';
import Button from 'antd/lib/button';
import notification from 'antd/lib/notification';
import Tooltip from 'antd/lib/tooltip';
import { ReloadOutlined } from '@ant-design/icons';

import { getCore } from './index';
import LinkControls from './panels/link-controls';
import AnnotationList from './panels/annotation-list';
import { getLinkIdFromState } from './utils/color';
import { LINK_ID_ATTR_NAME } from './consts';

const { Title, Text } = Typography;

function generateUUID(): string {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
        const r = (Math.random() * 16) | 0;
        const v = c === 'x' ? r : (r & 0x3) | 0x8;
        return v.toString(16);
    });
}

interface MatchParams {
    projectId?: string;
}

type Props = RouteComponentProps<MatchParams>;

function FusionPage(props: Props): JSX.Element {
    const { match } = props;
    const location = useLocation();
    const queryParams = useMemo(() => new URLSearchParams(location.search), [location.search]);

    const task2dParam = queryParams.get('task2d');
    const task3dParam = queryParams.get('task3d');
    const projectIdParam = match.params.projectId;
    const isTaskMode = !!(task2dParam && task3dParam);

    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [job2d, setJob2d] = useState<any>(null);
    const [job3d, setJob3d] = useState<any>(null);
    const [task2dId, setTask2dId] = useState<number | null>(null);
    const [task3dId, setTask3dId] = useState<number | null>(null);
    const [annotations2d, setAnnotations2d] = useState<any[]>([]);
    const [annotations3d, setAnnotations3d] = useState<any[]>([]);
    const [selected2d, setSelected2d] = useState<any>(null);
    const [selected3d, setSelected3d] = useState<any>(null);
    const [selectedLinkId, setSelectedLinkId] = useState<string | null>(null);
    const [headerLabel, setHeaderLabel] = useState<string>('Fusion Editor');
    const [refreshing, setRefreshing] = useState(false);

    // Build iframe URLs once we have job + task IDs
    const iframe2dUrl = useMemo(() => {
        if (!task2dId || !job2d) return null;
        return `/tasks/${task2dId}/jobs/${job2d.id}`;
    }, [task2dId, job2d]);

    const iframe3dUrl = useMemo(() => {
        if (!task3dId || !job3d) return null;
        return `/tasks/${task3dId}/jobs/${job3d.id}`;
    }, [task3dId, job3d]);

    // Load tasks/jobs on mount
    useEffect(() => {
        let cancelled = false;

        async function initFromTasks(t2dId: number, t3dId: number): Promise<void> {
            const core = getCore();
            const tasks2d = await core.tasks.get({ id: t2dId });
            const tasks3d = await core.tasks.get({ id: t3dId });
            const task2d = tasks2d[0];
            const task3d = tasks3d[0];

            if (!task2d) { setError(`2D task #${t2dId} not found`); return; }
            if (!task3d) { setError(`3D task #${t3dId} not found`); return; }

            const jobsList2d = await core.jobs.get({ taskID: task2d.id });
            const jobsList3d = await core.jobs.get({ taskID: task3d.id });

            if (cancelled) return;
            if (!jobsList2d.length || !jobsList3d.length) {
                setError('Could not find jobs for both 2D and 3D tasks.');
                return;
            }

            const [fullJob2d] = await core.jobs.get({ jobID: jobsList2d[0].id });
            const [fullJob3d] = await core.jobs.get({ jobID: jobsList3d[0].id });

            if (cancelled) return;
            setJob2d(fullJob2d);
            setJob3d(fullJob3d);
            setTask2dId(task2d.id);
            setTask3dId(task3d.id);
            setHeaderLabel(`Fusion Editor — 2D #${task2d.id} + 3D #${task3d.id}`);
        }

        async function initFromProject(pid: number): Promise<void> {
            const core = getCore();
            const [project] = await core.projects.get({ id: pid });
            if (!project) { setError(`Project #${pid} not found`); return; }

            const tasks = await core.tasks.get({ projectId: project.id });
            const task2d = tasks.find((t: any) => t.dimension === '2d');
            const task3d = tasks.find((t: any) => t.dimension === '3d');

            if (!task2d || !task3d) {
                setError('This project must contain at least one 2D task and one 3D task.');
                return;
            }

            const jobsList2d = await core.jobs.get({ taskID: task2d.id });
            const jobsList3d = await core.jobs.get({ taskID: task3d.id });

            if (cancelled) return;
            if (!jobsList2d.length || !jobsList3d.length) {
                setError('Could not find jobs for both 2D and 3D tasks.');
                return;
            }

            const [fullJob2d] = await core.jobs.get({ jobID: jobsList2d[0].id });
            const [fullJob3d] = await core.jobs.get({ jobID: jobsList3d[0].id });

            if (cancelled) return;
            setJob2d(fullJob2d);
            setJob3d(fullJob3d);
            setTask2dId(task2d.id);
            setTask3dId(task3d.id);
            setHeaderLabel(`Fusion Editor — Project #${pid}`);
        }

        async function init(): Promise<void> {
            try {
                if (isTaskMode) {
                    await initFromTasks(Number(task2dParam), Number(task3dParam));
                } else if (projectIdParam) {
                    await initFromProject(Number(projectIdParam));
                } else {
                    setError(
                        'Missing parameters. Use /fusion?task2d=<id>&task3d=<id> '
                        + 'or /fusion/<projectId>',
                    );
                }
            } catch (err: any) {
                if (!cancelled) {
                    setError(err?.message ?? String(err));
                }
            } finally {
                if (!cancelled) setLoading(false);
            }
        }

        init();
        return () => { cancelled = true; };
    }, [task2dParam, task3dParam, projectIdParam, isTaskMode]);

    // Fetch annotations for the linking panel (frame 0 initially, refreshable)
    const fetchAnnotations = useCallback(async (showNotification = false) => {
        if (!job2d || !job3d) return;
        try {
            // Clear cached annotations so we get fresh data from the server
            job2d.annotations.clear();
            job3d.annotations.clear();

            const [ann2d, ann3d] = await Promise.all([
                job2d.annotations.get(0),
                job3d.annotations.get(0),
            ]);
            setAnnotations2d(ann2d);
            setAnnotations3d(ann3d);
            if (showNotification) {
                notification.success({ message: 'Annotations refreshed' });
            }
        } catch (err: any) {
            notification.error({
                message: 'Failed to load annotations',
                description: err?.message,
            });
        }
    }, [job2d, job3d]);

    // Initial annotation fetch
    useEffect(() => {
        fetchAnnotations();
    }, [fetchAnnotations]);

    // When selectedLinkId changes, auto-select paired annotations
    useEffect(() => {
        if (!selectedLinkId) return;

        const paired2d = annotations2d.find(
            (s: any) => getLinkIdFromState(s) === selectedLinkId,
        );
        const paired3d = annotations3d.find(
            (s: any) => getLinkIdFromState(s) === selectedLinkId,
        );

        if (paired2d) setSelected2d(paired2d);
        if (paired3d) setSelected3d(paired3d);
    }, [selectedLinkId, annotations2d, annotations3d]);

    const handleSelectLinkId = useCallback((linkId: string | null) => {
        setSelectedLinkId(linkId);
    }, []);

    const handleRefresh = useCallback(async () => {
        setRefreshing(true);
        await fetchAnnotations(true);
        setRefreshing(false);
    }, [fetchAnnotations]);

    const handleLink = useCallback(async () => {
        if (!selected2d || !selected3d) return;

        try {
            const uuid = generateUUID();

            const spec2d = selected2d.label?.attributes?.find(
                (attr: any) => attr.name === LINK_ID_ATTR_NAME,
            );
            const spec3d = selected3d.label?.attributes?.find(
                (attr: any) => attr.name === LINK_ID_ATTR_NAME,
            );

            if (!spec2d || !spec3d) {
                notification.error({
                    message: 'Cannot link',
                    description: `Both labels must have a "${LINK_ID_ATTR_NAME}" attribute.`,
                });
                return;
            }

            selected2d.attributes[spec2d.id] = uuid;
            selected3d.attributes[spec3d.id] = uuid;

            await Promise.all([
                job2d.annotations.put([selected2d]),
                job3d.annotations.put([selected3d]),
            ]);

            await Promise.all([
                job2d.annotations.save(),
                job3d.annotations.save(),
            ]);

            setSelectedLinkId(uuid);
            await fetchAnnotations();

            notification.success({ message: 'Annotations linked & saved' });
        } catch (err: any) {
            notification.error({ message: 'Link failed', description: err?.message });
        }
    }, [selected2d, selected3d, job2d, job3d, fetchAnnotations]);

    const handleUnlink = useCallback(async () => {
        if (!selectedLinkId) return;

        try {
            const state2d = annotations2d.find(
                (s: any) => getLinkIdFromState(s) === selectedLinkId,
            );
            const state3d = annotations3d.find(
                (s: any) => getLinkIdFromState(s) === selectedLinkId,
            );

            const promises: Promise<any>[] = [];

            if (state2d) {
                const spec = state2d.label?.attributes?.find(
                    (attr: any) => attr.name === LINK_ID_ATTR_NAME,
                );
                if (spec) {
                    state2d.attributes[spec.id] = '';
                    promises.push(job2d.annotations.put([state2d]));
                }
            }

            if (state3d) {
                const spec = state3d.label?.attributes?.find(
                    (attr: any) => attr.name === LINK_ID_ATTR_NAME,
                );
                if (spec) {
                    state3d.attributes[spec.id] = '';
                    promises.push(job3d.annotations.put([state3d]));
                }
            }

            await Promise.all(promises);
            await Promise.all([
                job2d.annotations.save(),
                job3d.annotations.save(),
            ]);

            setSelectedLinkId(null);
            setSelected2d(null);
            setSelected3d(null);
            await fetchAnnotations();

            notification.success({ message: 'Annotations unlinked & saved' });
        } catch (err: any) {
            notification.error({ message: 'Unlink failed', description: err?.message });
        }
    }, [selectedLinkId, annotations2d, annotations3d, job2d, job3d, fetchAnnotations]);

    const handleSave = useCallback(async () => {
        if (!job2d || !job3d) return;
        try {
            await Promise.all([
                job2d.annotations.save(),
                job3d.annotations.save(),
            ]);
            notification.success({ message: 'Annotations saved' });
        } catch (err: any) {
            notification.error({ message: 'Save failed', description: err?.message });
        }
    }, [job2d, job3d]);

    // ---------- Render ----------

    if (loading) {
        return (
            <div style={{
                display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh',
            }}
            >
                <Spin size='large' tip='Loading project data…' />
            </div>
        );
    }

    if (error) {
        return (
            <div style={{ padding: 32 }}>
                <Alert type='error' showIcon message='Fusion Editor Error' description={error} />
            </div>
        );
    }

    return (
        <Layout style={{ height: '100vh', overflow: 'hidden' }}>
            {/* Header */}
            <div style={{
                padding: '6px 16px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                borderBottom: '1px solid #e8e8e8',
                background: '#fff',
                flexShrink: 0,
            }}
            >
                <Title level={4} style={{ margin: 0 }}>
                    {headerLabel}
                </Title>
                <Tooltip title='Reload annotations from both editors to sync link state'>
                    <Button
                        icon={<ReloadOutlined spin={refreshing} />}
                        onClick={handleRefresh}
                        loading={refreshing}
                    >
                        Refresh Annotations
                    </Button>
                </Tooltip>
            </div>

            {/* Side-by-side annotation editors */}
            <Row style={{ flex: 1, minHeight: 0, overflow: 'hidden' }} gutter={0}>
                <Col span={12} style={{ height: '100%', borderRight: '2px solid #d9d9d9' }}>
                    <div style={{
                        padding: '4px 8px',
                        background: '#f5f5f5',
                        borderBottom: '1px solid #e8e8e8',
                        flexShrink: 0,
                    }}
                    >
                        <Text strong>2D Editor</Text>
                        {iframe2dUrl && (
                            <Text type='secondary' style={{ marginLeft: 8 }}>
                                {iframe2dUrl}
                            </Text>
                        )}
                    </div>
                    {iframe2dUrl ? (
                        <iframe
                            src={iframe2dUrl}
                            title='2D Annotation Editor'
                            style={{
                                width: '100%',
                                height: 'calc(100% - 30px)',
                                border: 'none',
                            }}
                        />
                    ) : (
                        <div style={{
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            height: '100%',
                        }}
                        >
                            <Spin tip='Loading 2D editor…' />
                        </div>
                    )}
                </Col>
                <Col span={12} style={{ height: '100%' }}>
                    <div style={{
                        padding: '4px 8px',
                        background: '#f5f5f5',
                        borderBottom: '1px solid #e8e8e8',
                        flexShrink: 0,
                    }}
                    >
                        <Text strong>3D Editor</Text>
                        {iframe3dUrl && (
                            <Text type='secondary' style={{ marginLeft: 8 }}>
                                {iframe3dUrl}
                            </Text>
                        )}
                    </div>
                    {iframe3dUrl ? (
                        <iframe
                            src={iframe3dUrl}
                            title='3D Annotation Editor'
                            style={{
                                width: '100%',
                                height: 'calc(100% - 30px)',
                                border: 'none',
                            }}
                        />
                    ) : (
                        <div style={{
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            height: '100%',
                        }}
                        >
                            <Spin tip='Loading 3D editor…' />
                        </div>
                    )}
                </Col>
            </Row>

            {/* Link controls */}
            <LinkControls
                selected2d={selected2d}
                selected3d={selected3d}
                selectedLinkId={selectedLinkId}
                onLink={handleLink}
                onUnlink={handleUnlink}
                onSave={handleSave}
            />

            {/* Annotation list */}
            <AnnotationList
                annotations2d={annotations2d}
                annotations3d={annotations3d}
                selectedLinkId={selectedLinkId}
                onSelectLinkId={handleSelectLinkId}
            />
        </Layout>
    );
}

export default FusionPage;
