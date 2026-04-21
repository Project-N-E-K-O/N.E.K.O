import Ammo from 'ammojs-typed';
import { Object3D, Matrix4, Vector3, Quaternion, MeshBasicMaterial, Color, Mesh, SphereGeometry, CapsuleGeometry, BoxGeometry, Euler, Bone } from 'three';
import { PmxObject } from '@moeru/three-mmd';


class Constraint {
  bodyA;
  bodyB;
  constraint;
  manager;
  mesh;
  params;
  world;
  constructor(mesh, world, bodyA, bodyB, params, manager) {
    this.mesh = mesh;
    this.world = world;
    this.bodyA = bodyA;
    this.bodyB = bodyB;
    this.params = params;
    this.manager = manager;
    this._init();
  }
  _init() {
    const manager = this.manager;
    const params = this.params;
    const bodyA = this.bodyA;
    const bodyB = this.bodyB;
    const form = manager.allocTransform();
    manager.setIdentity(form);
    manager.setOriginFromArray3(form, params.position);
    manager.setBasisFromArray3(form, params.rotation);
    const formA = manager.allocTransform();
    const formB = manager.allocTransform();
    bodyA.body.getMotionState().getWorldTransform(formA);
    bodyB.body.getMotionState().getWorldTransform(formB);
    const formInverseA = manager.inverseTransform(formA);
    const formInverseB = manager.inverseTransform(formB);
    const formA2 = manager.multiplyTransforms(formInverseA, form);
    const formB2 = manager.multiplyTransforms(formInverseB, form);
    const constraint = new Ammo.btGeneric6DofSpringConstraint(bodyA.body, bodyB.body, formA2, formB2, true);
    const lll = manager.allocVector3();
    const lul = manager.allocVector3();
    const all = manager.allocVector3();
    const aul = manager.allocVector3();
    lll.setValue(...params.positionMin);
    lul.setValue(...params.positionMax);
    // Angular limit clamping: lock axes with range < 5° to eliminate micro-oscillation sources
    const angMin = [...params.rotationMin];
    const angMax = [...params.rotationMax];
    const CLAMP_THRESHOLD = 0.0872665; // 5° in radians
    for (let i = 0; i < 3; i++) {
      const range = angMax[i] - angMin[i];
      if (range >= 0 && range < CLAMP_THRESHOLD) {
        const mid = (angMin[i] + angMax[i]) * 0.5;
        angMin[i] = mid;
        angMax[i] = mid;
      }
    }
    all.setValue(...angMin);
    aul.setValue(...angMax);
    constraint.setLinearLowerLimit(lll);
    constraint.setLinearUpperLimit(lul);
    constraint.setAngularLowerLimit(all);
    constraint.setAngularUpperLimit(aul);
    for (let i = 0; i < 3; i++) {
      if (params.springPosition[i] !== 0) {
        constraint.enableSpring(i, true);
        // 高密度特化 1: 暴力拉紧弹簧 (2.5x)
        // 在降低了物理频率后，增强弹簧强度以死死拽住易散架的网格
        constraint.setStiffness(i, params.springPosition[i] * 2.5);
        // Ammo.js 可能不会像 Bullet C++ 一样默认 m_springDamping=1.0，
        // 显式设置临界阻尼，防止低阻尼刚体（飘带）在弹簧力下永远振荡
        if (constraint.setDamping) constraint.setDamping(i, 1.0);
      }
    }
    for (let i = 0; i < 3; i++) {
      if (params.springRotation[i] !== 0) {
        constraint.enableSpring(i + 3, true);
        // 高密度特化 1: 暴力拉紧弹簧 (2.5x)
        constraint.setStiffness(i + 3, params.springRotation[i] * 2.5);
        if (constraint.setDamping) constraint.setDamping(i + 3, 1.0);
      }
    }
    if (constraint.setParam !== void 0) {
      for (let i = 0; i < 6; i++) {
        // BT_CONSTRAINT_STOP_ERP: Define error reduction to allow natural spring force recovery
        constraint.setParam(2, 0.2, i);
        // 高密度特化 2: 开启关节泄压阀 (CFM)
        // 给关节注入微弱的弹性 (0.015)，在受到极端排斥力时允许微小形变，防止它“硬生生被扯断”
        constraint.setParam(4, 0.015, i);
      }
    }
    this.world.addConstraint(constraint, true);
    // MMD 兼容性修复：禁用 m_useOffsetForConstraintFrame
    if (Constraint._useFrameOffsetDisabled === undefined) {
      Constraint._useFrameOffsetDisabled = false;
      Constraint._heapOffset = -1;
      if (typeof constraint.setUseFrameOffset === 'function') {
        constraint.setUseFrameOffset(false);
        Constraint._useFrameOffsetDisabled = true;
        Constraint._useDirectMethod = true;
        console.log('[MMD Physics] setUseFrameOffset(false) applied via direct method');
      } else if (typeof Ammo.getPointer === 'function') {
        try {
          const tmpForm = manager.allocTransform();
          manager.setIdentity(tmpForm);
          const cTrue = new Ammo.btGeneric6DofSpringConstraint(bodyA.body, bodyB.body, tmpForm, tmpForm, true);
          const cFalse = new Ammo.btGeneric6DofSpringConstraint(bodyA.body, bodyB.body, tmpForm, tmpForm, false);
          const ptrT = Ammo.getPointer(cTrue);
          const ptrF = Ammo.getPointer(cFalse);
          const heap = Ammo.HEAPU8;
          if (heap) {
            for (let off = 1200; off < 1400; off++) {
              if (heap[ptrT + off] === 1 && heap[ptrF + off] === 0) {
                Constraint._heapOffset = off;
                console.log(`[MMD Physics] useFrameOffset heap detected at offset ${off}, applying fix`);
                if (heap[ptrT + off] === 1) {
                  Constraint._useFrameOffsetDisabled = true;
                }
                break;
              }
            }
            if (!Constraint._useFrameOffsetDisabled) {
              console.warn('[MMD Physics] useFrameOffset heap offset not found, physics may be unstable');
            }
          }
          Ammo.destroy(cTrue);
          Ammo.destroy(cFalse);
          manager.freeTransform(tmpForm);
        } catch (e) {
          console.warn('[MMD Physics] useFrameOffset detection failed:', e);
        }
      }
    }
    if (Constraint._useFrameOffsetDisabled) {
      if (Constraint._useDirectMethod) {
        constraint.setUseFrameOffset(false);
      } else if (Constraint._heapOffset > 0) {
        const ptr = Ammo.getPointer(constraint);
        Ammo.HEAPU8[ptr + Constraint._heapOffset] = 0;
      }
    }
    this.constraint = constraint;
    manager.freeTransform(form);
    manager.freeTransform(formA);
    manager.freeTransform(formB);
    manager.freeTransform(formInverseA);
    manager.freeTransform(formInverseB);
    manager.freeTransform(formA2);
    manager.freeTransform(formB2);
    manager.freeVector3(lll);
    manager.freeVector3(lul);
    manager.freeVector3(all);
    manager.freeVector3(aul);
  }
}

class MMDPhysicsHelper extends Object3D {
  materials;
  physics;
  root;
  _matrixWorldInv = new Matrix4();
  _position = new Vector3();
  _quaternion = new Quaternion();
  _scale = new Vector3();
  /**
   * Visualize Rigid bodies
   */
  constructor(mesh, physics) {
    super();
    this.root = mesh;
    this.physics = physics;
    this.matrix.copy(mesh.matrixWorld);
    this.matrixAutoUpdate = false;
    this.materials = [
      new MeshBasicMaterial({
        color: new Color(16746632),
        depthTest: false,
        depthWrite: false,
        opacity: 0.25,
        transparent: true,
        wireframe: true
      }),
      new MeshBasicMaterial({
        color: new Color(8978312),
        depthTest: false,
        depthWrite: false,
        opacity: 0.25,
        transparent: true,
        wireframe: true
      }),
      new MeshBasicMaterial({
        color: new Color(8947967),
        depthTest: false,
        depthWrite: false,
        opacity: 0.25,
        transparent: true,
        wireframe: true
      })
    ];
    this._init();
  }
  _init() {
    const bodies = this.physics.bodies;
    const createGeometry = (param) => {
      const [width, height, depth] = param.shapeSize;
      switch (param.shapeType) {
        case PmxObject.RigidBody.ShapeType.Box:
          return new BoxGeometry(width, height, depth, 8, 8, 8);
        case PmxObject.RigidBody.ShapeType.Capsule:
          return new CapsuleGeometry(width, height, 8, 16);
        case PmxObject.RigidBody.ShapeType.Sphere:
          return new SphereGeometry(width, 16, 8);
        default:
          return void 0;
      }
    };
    for (let i = 0, il = bodies.length; i < il; i++) {
      const param = bodies[i].params;
      this.add(new Mesh(createGeometry(param), this.materials[param.physicsMode]));
    }
  }
  /**
   * Frees the GPU-related resources allocated by this instance. Call this method whenever this instance is no longer used in your app.
   */
  dispose() {
    const materials = this.materials;
    const children = this.children;
    for (let i = 0; i < materials.length; i++) {
      materials[i].dispose();
    }
    for (let i = 0; i < children.length; i++) {
      const child = children[i];
      if ("isMesh" in child && child.isMesh === true)
        child.geometry.dispose();
    }
  }
  // private method
  /**
   * Updates Rigid Bodies visualization.
   */
  updateMatrixWorld(force) {
    const mesh = this.root;
    if (this.visible) {
      const bodies = this.physics.bodies;
      this._matrixWorldInv.copy(mesh.matrixWorld).decompose(this._position, this._quaternion, this._scale).compose(this._position, this._quaternion, this._scale.set(1, 1, 1)).invert();
      for (let i = 0, il = bodies.length; i < il; i++) {
        const body = bodies[i].body;
        const child = this.children[i];
        const tr = body.getCenterOfMassTransform();
        const origin = tr.getOrigin();
        const rotation = tr.getRotation();
        child.position.set(origin.x(), origin.y(), origin.z()).applyMatrix4(this._matrixWorldInv);
        child.quaternion.setFromRotationMatrix(this._matrixWorldInv).multiply(
          this._quaternion.set(rotation.x(), rotation.y(), rotation.z(), rotation.w())
        );
      }
    }
    this.matrix.copy(mesh.matrixWorld).decompose(this._position, this._quaternion, this._scale).compose(this._position, this._quaternion, this._scale.set(1, 1, 1));
    super.updateMatrixWorld(force);
  }
}

class ResourceManager {
  quaternions;
  threeEulers;
  threeMatrix4s;
  threeQuaternions;
  threeVector3s;
  transforms;
  vector3s;
  constructor() {
    this.threeVector3s = [];
    this.threeMatrix4s = [];
    this.threeQuaternions = [];
    this.threeEulers = [];
    this.transforms = [];
    this.quaternions = [];
    this.vector3s = [];
  }
  addVector3(v1, v2) {
    const v = this.allocVector3();
    v.setValue(v1.x() + v2.x(), v1.y() + v2.y(), v1.z() + v2.z());
    return v;
  }
  allocQuaternion() {
    return this.quaternions.length > 0 ? this.quaternions.pop() : new Ammo.btQuaternion(0, 0, 0, 0);
  }
  allocThreeEuler() {
    return this.threeEulers.length > 0 ? this.threeEulers.pop() : new Euler();
  }
  allocThreeMatrix4() {
    return this.threeMatrix4s.length > 0 ? this.threeMatrix4s.pop() : new Matrix4();
  }
  allocThreeQuaternion() {
    return this.threeQuaternions.length > 0 ? this.threeQuaternions.pop() : new Quaternion();
  }
  allocThreeVector3() {
    return this.threeVector3s.length > 0 ? this.threeVector3s.pop() : new Vector3();
  }
  allocTransform() {
    return this.transforms.length > 0 ? this.transforms.pop() : new Ammo.btTransform();
  }
  allocVector3() {
    return this.vector3s.length > 0 ? this.vector3s.pop() : new Ammo.btVector3();
  }
  // TODO: strict type
  columnOfMatrix3(m, i) {
    const v = this.allocVector3();
    v.setValue(m[i + 0], m[i + 3], m[i + 6]);
    return v;
  }
  copyOrigin(t1, t2) {
    const o = t2.getOrigin();
    this.setOrigin(t1, o);
  }
  dotVectors3(v1, v2) {
    return v1.x() * v2.x() + v1.y() * v2.y() + v1.z() * v2.z();
  }
  freeQuaternion(q) {
    this.quaternions.push(q);
  }
  freeThreeEuler(e) {
    this.threeEulers.push(e);
  }
  freeThreeMatrix4(m) {
    this.threeMatrix4s.push(m);
  }
  freeThreeQuaternion(q) {
    this.threeQuaternions.push(q);
  }
  freeThreeVector3(v) {
    this.threeVector3s.push(v);
  }
  freeTransform(t) {
    this.transforms.push(t);
  }
  freeVector3(v) {
    this.vector3s.push(v);
  }
  getBasis(t) {
    const q = this.allocQuaternion();
    t.getBasis().getRotation(q);
    return q;
  }
  getBasisAsMatrix3(t) {
    const q = this.getBasis(t);
    const m = this.quaternionToMatrix3(q);
    this.freeQuaternion(q);
    return m;
  }
  getOrigin(t) {
    return t.getOrigin();
  }
  inverseTransform(t) {
    const t2 = this.allocTransform();
    const m1 = this.getBasisAsMatrix3(t);
    const o = this.getOrigin(t);
    const m2 = this.transposeMatrix3(m1);
    const v1 = this.negativeVector3(o);
    const v2 = this.multiplyMatrix3ByVector3(m2, v1);
    this.setOrigin(t2, v2);
    this.setBasisFromMatrix3(t2, m2);
    this.freeVector3(v1);
    this.freeVector3(v2);
    return t2;
  }
  // TODO: strict type
  matrix3ToQuaternion(m) {
    const t = m[0] + m[4] + m[8];
    let s, w, x, y, z;
    if (t > 0) {
      s = Math.sqrt(t + 1) * 2;
      w = 0.25 * s;
      x = (m[7] - m[5]) / s;
      y = (m[2] - m[6]) / s;
      z = (m[3] - m[1]) / s;
    } else if (m[0] > m[4] && m[0] > m[8]) {
      s = Math.sqrt(1 + m[0] - m[4] - m[8]) * 2;
      w = (m[7] - m[5]) / s;
      x = 0.25 * s;
      y = (m[1] + m[3]) / s;
      z = (m[2] + m[6]) / s;
    } else if (m[4] > m[8]) {
      s = Math.sqrt(1 + m[4] - m[0] - m[8]) * 2;
      w = (m[2] - m[6]) / s;
      x = (m[1] + m[3]) / s;
      y = 0.25 * s;
      z = (m[5] + m[7]) / s;
    } else {
      s = Math.sqrt(1 + m[8] - m[0] - m[4]) * 2;
      w = (m[3] - m[1]) / s;
      x = (m[2] + m[6]) / s;
      y = (m[5] + m[7]) / s;
      z = 0.25 * s;
    }
    const q = this.allocQuaternion();
    q.setX(x);
    q.setY(y);
    q.setZ(z);
    q.setW(w);
    return q;
  }
  // TODO: strict type
  multiplyMatrices3(m1, m2) {
    const m3 = [];
    const v10 = this.rowOfMatrix3(m1, 0);
    const v11 = this.rowOfMatrix3(m1, 1);
    const v12 = this.rowOfMatrix3(m1, 2);
    const v20 = this.columnOfMatrix3(m2, 0);
    const v21 = this.columnOfMatrix3(m2, 1);
    const v22 = this.columnOfMatrix3(m2, 2);
    m3[0] = this.dotVectors3(v10, v20);
    m3[1] = this.dotVectors3(v10, v21);
    m3[2] = this.dotVectors3(v10, v22);
    m3[3] = this.dotVectors3(v11, v20);
    m3[4] = this.dotVectors3(v11, v21);
    m3[5] = this.dotVectors3(v11, v22);
    m3[6] = this.dotVectors3(v12, v20);
    m3[7] = this.dotVectors3(v12, v21);
    m3[8] = this.dotVectors3(v12, v22);
    this.freeVector3(v10);
    this.freeVector3(v11);
    this.freeVector3(v12);
    this.freeVector3(v20);
    this.freeVector3(v21);
    this.freeVector3(v22);
    return m3;
  }
  // TODO: strict type
  multiplyMatrix3ByVector3(m, v) {
    const v4 = this.allocVector3();
    const v0 = this.rowOfMatrix3(m, 0);
    const v1 = this.rowOfMatrix3(m, 1);
    const v2 = this.rowOfMatrix3(m, 2);
    const x = this.dotVectors3(v0, v);
    const y = this.dotVectors3(v1, v);
    const z = this.dotVectors3(v2, v);
    v4.setValue(x, y, z);
    this.freeVector3(v0);
    this.freeVector3(v1);
    this.freeVector3(v2);
    return v4;
  }
  multiplyTransforms(t1, t2) {
    const t = this.allocTransform();
    this.setIdentity(t);
    const m1 = this.getBasisAsMatrix3(t1);
    const m2 = this.getBasisAsMatrix3(t2);
    const o1 = this.getOrigin(t1);
    const o2 = this.getOrigin(t2);
    const v1 = this.multiplyMatrix3ByVector3(m1, o2);
    const v2 = this.addVector3(v1, o1);
    this.setOrigin(t, v2);
    const m3 = this.multiplyMatrices3(m1, m2);
    this.setBasisFromMatrix3(t, m3);
    this.freeVector3(v1);
    this.freeVector3(v2);
    return t;
  }
  negativeVector3(v) {
    const v2 = this.allocVector3();
    v2.setValue(-v.x(), -v.y(), -v.z());
    return v2;
  }
  quaternionToMatrix3(q) {
    const m = [];
    const x = q.x();
    const y = q.y();
    const z = q.z();
    const w = q.w();
    const xx = x * x;
    const yy = y * y;
    const zz = z * z;
    const xy = x * y;
    const yz = y * z;
    const zx = z * x;
    const xw = x * w;
    const yw = y * w;
    const zw = z * w;
    m[0] = 1 - 2 * (yy + zz);
    m[1] = 2 * (xy - zw);
    m[2] = 2 * (zx + yw);
    m[3] = 2 * (xy + zw);
    m[4] = 1 - 2 * (zz + xx);
    m[5] = 2 * (yz - xw);
    m[6] = 2 * (zx - yw);
    m[7] = 2 * (yz + xw);
    m[8] = 1 - 2 * (xx + yy);
    return m;
  }
  // TODO: strict type
  rowOfMatrix3(m, i) {
    const v = this.allocVector3();
    v.setValue(m[i * 3 + 0], m[i * 3 + 1], m[i * 3 + 2]);
    return v;
  }
  setBasis(t, q) {
    t.setRotation(q);
  }
  setBasisFromArray3(t, a) {
    const thQ = this.allocThreeQuaternion();
    const thE = this.allocThreeEuler();
    thE.set(a[0], a[1], a[2]);
    this.setBasisFromThreeQuaternion(t, thQ.setFromEuler(thE));
    this.freeThreeEuler(thE);
    this.freeThreeQuaternion(thQ);
  }
  // TODO: strict type
  setBasisFromMatrix3(t, m) {
    const q = this.matrix3ToQuaternion(m);
    this.setBasis(t, q);
    this.freeQuaternion(q);
  }
  setBasisFromThreeQuaternion(t, a) {
    const q = this.allocQuaternion();
    q.setX(a.x);
    q.setY(a.y);
    q.setZ(a.z);
    q.setW(a.w);
    this.setBasis(t, q);
    this.freeQuaternion(q);
  }
  setIdentity(t) {
    t.setIdentity();
  }
  setOrigin(t, v) {
    t.getOrigin().setValue(v.x(), v.y(), v.z());
  }
  setOriginFromArray3(t, a) {
    t.getOrigin().setValue(a[0], a[1], a[2]);
  }
  setOriginFromThreeVector3(t, v) {
    t.getOrigin().setValue(v.x, v.y, v.z);
  }
  // TODO: strict type
  transposeMatrix3(m) {
    const m2 = [];
    m2[0] = m[0];
    m2[1] = m[3];
    m2[2] = m[6];
    m2[3] = m[1];
    m2[4] = m[4];
    m2[5] = m[7];
    m2[6] = m[2];
    m2[7] = m[5];
    m2[8] = m[8];
    return m2;
  }
}

class RigidBody {
  body;
  bone;
  boneOffsetForm;
  boneOffsetFormInverse;
  manager;
  mesh;
  params;
  world;
  physics;
  restPos;
  constructor(mesh, world, params, manager) {
    this.mesh = mesh;
    this.world = world;
    this.params = params;

    // [终极修复] 绕过导致空间空间撕裂的元凶：强制降级 Mode 2
    // WebGL 环境下强制每帧执行骨骼位置对齐(Mode 2)会引发与高强度弹簧抗衡的灾难性“鬼畜”和拉扯起刺。
    // 暴力转换为 Mode 1 (纯物理演算)，依靠弹簧强刚度完美自我维持结构，彻底拒绝干预物理。
    if (this.params.physicsMode === 2) {
      this.params.physicsMode = 1;
    }

    this.manager = manager;
    // 安全获取影子引用：优先从 mesh 获取，否则跳过
    this.physics = mesh.mmdPhysics || null;
    const generateShape = (p) => {
      // 高密度特化 3: 刚体物理瘦身
      // 我们把动态衣服/头发刚体的碰撞体积缩小 20%，避免初始状态下过度拥挤和重叠产生的巨大推斥力
      const sf = (p.physicsMode !== 0) ? 0.8 : 1.0;
      const width = p.shapeSize[0] * sf;
      const height = p.shapeSize[1] * sf;
      const depth = p.shapeSize[2] * sf;

      switch (p.shapeType) {
        case PmxObject.RigidBody.ShapeType.Box:
          return new Ammo.btBoxShape(new Ammo.btVector3(width, height, depth));
        case PmxObject.RigidBody.ShapeType.Capsule:
          return new Ammo.btCapsuleShape(width, height);
        case PmxObject.RigidBody.ShapeType.Sphere:
          return new Ammo.btSphereShape(width);
      }
    };
    const bone = this.params.boneIndex === -1 ? new Bone() : this.mesh.skeleton.bones[this.params.boneIndex];
    const boneName = bone.name || '';
    const shape = generateShape(this.params);
    // 高密度补丁：将碰撞余量从默认的 0.04 压缩至 0.01，为彼此贴近的密集刚体换取生存空间。
    if (shape.setMargin) shape.setMargin(0.01);

    const weight = this.params.physicsMode === 0 ? 0 : this.params.mass;
    const localInertia = this.manager.allocVector3();
    localInertia.setValue(0, 0, 0);
    if (weight !== 0)
      shape.calculateLocalInertia(weight, localInertia);
    const offsetLocal = bone.worldToLocal(
      new Vector3().fromArray(this.params.shapePosition).applyMatrix4(this.mesh.matrixWorld)
    );
    const shapeQuat = new Quaternion().setFromEuler(new Euler(
      this.params.shapeRotation[0],
      this.params.shapeRotation[1],
      this.params.shapeRotation[2]
    ));
    const meshWorldQuat = this.mesh.getWorldQuaternion(new Quaternion());
    const boneWorldQuat = bone.getWorldQuaternion(new Quaternion());
    const offsetRotation = boneWorldQuat.clone().invert().multiply(meshWorldQuat).multiply(shapeQuat);
    const boneOffsetForm = this.manager.allocTransform();
    this.manager.setIdentity(boneOffsetForm);
    this.manager.setOriginFromThreeVector3(boneOffsetForm, offsetLocal);
    this.manager.setBasisFromThreeQuaternion(boneOffsetForm, offsetRotation);
    const vector = this.manager.allocThreeVector3();
    const rotation = this.manager.allocThreeQuaternion();
    const boneForm = this.manager.allocTransform();
    this.manager.setIdentity(boneForm);
    this.manager.setOriginFromThreeVector3(boneForm, bone.getWorldPosition(vector));
    this.manager.setBasisFromThreeQuaternion(boneForm, bone.getWorldQuaternion(rotation));
    const form = this.manager.multiplyTransforms(boneForm, boneOffsetForm);
    const state = new Ammo.btDefaultMotionState(form);
    const info = new Ammo.btRigidBodyConstructionInfo(weight, state, shape, localInertia);
    info.set_m_friction(this.params.friction || 0.5);
    // 注入微量灵动感：恢复极小比例的反弹力，防止动作过于死板。
    info.set_m_restitution(0.1);
    // Enable Bullet's built-in additional damping to reduce micro-oscillations
    if (typeof info.set_m_additionalDamping === 'function') {
      info.set_m_additionalDamping(true);
    }
    const body = new Ammo.btRigidBody(info);
    if (this.params.physicsMode === 0) {
      body.setCollisionFlags(body.getCollisionFlags() | 2);
    }
    // Apply higher damping (5.0x) to low-mass rigid bodies (<1.0) to prevent high-frequency jitter
    const dampingScale = (weight > 0 && weight < 1.0) ? 5.0 : 1.0;
    body.setDamping(this.params.linearDamping * dampingScale, this.params.angularDamping * dampingScale);
    body.setSleepingThresholds(0, 0);
    this.world.addRigidBody(body, 1 << this.params.collisionGroup, this.params.collisionMask);
    // 检测是否为 IK 骨骼或关键关节点
    this.isIKBone = boneName.toLowerCase().includes('ik') || (this.params.physicsMode === 0);

    this.body = body;
    this.bone = bone;
    this.boneOffsetForm = boneOffsetForm;
    this.boneOffsetFormInverse = this.manager.inverseTransform(boneOffsetForm);
    this.manager.freeVector3(localInertia);
    this.manager.freeTransform(form);
    this.manager.freeTransform(boneForm);
    this.manager.freeThreeVector3(vector);
    this.manager.freeThreeQuaternion(rotation);
  }
  _getBoneTransform() {
    const manager = this.manager;
    const p = manager.allocThreeVector3();
    const q = manager.allocThreeQuaternion();
    const s = manager.allocThreeVector3();
    this.bone.matrixWorld.decompose(p, q, s);
    const tr = manager.allocTransform();
    manager.setOriginFromThreeVector3(tr, p);
    manager.setBasisFromThreeQuaternion(tr, q);
    const form = manager.multiplyTransforms(tr, this.boneOffsetForm);
    manager.freeTransform(tr);
    manager.freeThreeVector3(s);
    manager.freeThreeQuaternion(q);
    manager.freeThreeVector3(p);
    return form;
  }
  _getWorldTransformForBone() {
    const manager = this.manager;
    const tr = this.body.getCenterOfMassTransform();
    return manager.multiplyTransforms(tr, this.boneOffsetFormInverse);
  }
  _setPositionFromBone() {
    const manager = this.manager;
    const form = this._getBoneTransform();
    // 100% Zero-latency sync for Kinematic bones to prevent bone-collider mismatch
    this.body.setWorldTransform(form);
    manager.freeTransform(form);
  }
  _setTransformFromBone() {
    const manager = this.manager;
    const form = this._getBoneTransform();
    this.body.setWorldTransform(form);
    manager.freeTransform(form);
  }
  _updateBonePosition() {
    const manager = this.manager;
    const tr = this._getWorldTransformForBone();
    const thV = manager.allocThreeVector3();
    const o = manager.getOrigin(tr);
    thV.set(o.x(), o.y(), o.z());
    if (this.bone.parent)
      this.bone.parent.worldToLocal(thV);

    // 零延迟同步 (Zero-Lag Position)：彻底杜绝因延迟产生的“拉伸”
    if (this.isIKBone) {
      this.bone.position.lerp(thV, 0.05);
    } else {
      this.bone.position.copy(thV);
    }

    manager.freeThreeVector3(thV);
    manager.freeTransform(tr);
  }
  _updateBonePositionSoft() {
    const manager = this.manager;
    const tr = this._getWorldTransformForBone();
    const thV = manager.allocThreeVector3();
    const o = manager.getOrigin(tr);
    thV.set(o.x(), o.y(), o.z());
    if (this.bone.parent)
      this.bone.parent.worldToLocal(thV);
    // 动态 alpha：偏差小时快速收敛（减少穿模），偏差大时慢速跟随（避免拉长）
    const dist = this.bone.position.distanceTo(thV);
    const alpha = dist < 0.1 ? 0.06 : dist < 0.5 ? 0.02 : 0.005;
    this.bone.position.lerp(thV, alpha);
    manager.freeThreeVector3(thV);
    manager.freeTransform(tr);
  }
  _updateBoneRotation() {
    const manager = this.manager;
    const tr = this._getWorldTransformForBone();
    const q = manager.getBasis(tr);
    const thQ = manager.allocThreeQuaternion();
    const thQ2 = manager.allocThreeQuaternion();
    const thQ3 = manager.allocThreeQuaternion();
    thQ.set(q.x(), q.y(), q.z(), q.w());
    thQ2.setFromRotationMatrix(this.bone.matrixWorld);
    thQ2.conjugate();
    thQ2.multiply(thQ);
    thQ3.setFromRotationMatrix(this.bone.matrix);

    // 1:1 Absolute Sync: Apply exact physics rotation to bone to eliminate viscosity/lag
    const targetQ = thQ2.multiply(thQ3).normalize();
    this.bone.quaternion.copy(targetQ);

    manager.freeThreeQuaternion(thQ);
    manager.freeThreeQuaternion(thQ2);
    manager.freeThreeQuaternion(thQ3);
    manager.freeQuaternion(q);
    manager.freeTransform(tr);
  }
  /**
   * Resets rigid body transform to the current bone's.
   */
  reset() {
    this._setTransformFromBone();
    return this;
  }
  /**
   * Updates bone from the current rigid body's transform.
   */
  updateBone() {
    if (this.params.physicsMode === 0 || this.params.boneIndex === -1)
      return this;
    this._updateBoneRotation();
    // 柔性位置同步：不硬覆盖 position（会导致拉长），而是缓慢趋向物理位置
    // 保持碰撞一致性的同时避免视觉拉伸
    if (this.params.physicsMode === 1)
      this._updateBonePositionSoft();
    this.bone.updateMatrixWorld(true);
    if (this.params.physicsMode === 2)
      this._setPositionFromBone();
    return this;
  }
  /**
   * Updates rigid body's transform from the current bone.
   */
  updateFromBone() {
    if (this.params.boneIndex !== -1 && this.params.physicsMode === 0)
      this._setTransformFromBone();
    return this;
  }
}

class MMDPhysics {
  bodies;
  constraints;
  gravity;
  manager;
  maxStepNum;
  mesh;
  unitStep;
  world;
  constructor(mesh, rigidBodyParams, constraintParams = [], params = {}) {
    this.mesh = mesh;
    // 在 mesh 上挂载影子引用，解决初始化签名冲突问题
    this.mesh.mmdPhysics = this;

    this.manager = new ResourceManager();
    // Reduced physics calculation from 1/120 to 1/60 to save CPU/Browser performance
    this.unitStep = params.unitStep !== void 0 ? params.unitStep : 1 / 60;
    // Lowered max step to prevent physics lag-death spirals
    this.maxStepNum = params.maxStepNum !== void 0 ? params.maxStepNum : 3;
    this.gravity = new Vector3(0, -9.8 * 10, 0);
    if (params.gravity !== void 0)
      this.gravity.copy(params.gravity);

    // 运动追踪系统：记录网格在世界坐标系下的位移
    this._lastMeshPos = new Vector3();
    mesh.getWorldPosition(this._lastMeshPos);
    this._lastMeshQuat = new Quaternion();
    mesh.getWorldQuaternion(this._lastMeshQuat);

    if (params.world !== void 0)
      this.world = params.world;
    this.bodies = [];
    this.constraints = [];

    this._init(mesh, rigidBodyParams, constraintParams);
  }
  _createWorld() {
    const config = new Ammo.btDefaultCollisionConfiguration();
    const dispatcher = new Ammo.btCollisionDispatcher(config);
    const cache = new Ammo.btDbvtBroadphase();
    const solver = new Ammo.btSequentialImpulseConstraintSolver();
    const world = new Ammo.btDiscreteDynamicsWorld(dispatcher, cache, solver, config);
    const solverInfo = world.getSolverInfo();
    // Releasing solver brute-force: rigid bodies are naturally stable now, returning to 10 iterations
    solverInfo.set_m_numIterations(10);
    // 启用分割冲量 (Split Impulse)：位移修正不引入额外动能，抑制“炸模”现象。
    solverInfo.set_m_splitImpulse(true);
    solverInfo.set_m_splitImpulsePenetrationThreshold(-0.04);

    // Invisible Ground Plane: Prevents dresses/hair from clipping through Y=0
    // [致命Bug修复] 使用巨型 Box 替代 StaticPlane，因为 Bullet 中的无限平面无法动态旋转
    const groundTransform = new Ammo.btTransform();
    groundTransform.setIdentity();
    // 向下偏移 1 单位，考虑到 BoxShape 高度 half-extent 为 1，这样盒子顶部正好顶在 Y=0
    groundTransform.setOrigin(new Ammo.btVector3(0, -1, 0));
    const groundShape = new Ammo.btBoxShape(new Ammo.btVector3(100, 1, 100));
    const groundMass = 0; // mass = 0 代表静态无限大刚体
    const groundLocalInertia = new Ammo.btVector3(0, 0, 0);
    const groundMotionState = new Ammo.btDefaultMotionState(groundTransform);
    const groundInfo = new Ammo.btRigidBodyConstructionInfo(groundMass, groundMotionState, groundShape, groundLocalInertia);
    groundInfo.set_m_friction(0.8);
    groundInfo.set_m_restitution(0.1);
    const groundBody = new Ammo.btRigidBody(groundInfo);

    world.addRigidBody(groundBody, 1, -1);
    this.groundBody = groundBody; // Bound for dynamic positional tracking

    return world;
  }
  _init(mesh, rigidBodyParams, constraintParams) {
    const manager = this.manager;
    const parent = mesh.parent;
    if (parent !== null)
      mesh.parent = null;
    const currentPosition = manager.allocThreeVector3();
    const currentQuaternion = manager.allocThreeQuaternion();
    const currentScale = manager.allocThreeVector3();
    currentPosition.copy(mesh.position);
    currentQuaternion.copy(mesh.quaternion);
    currentScale.copy(mesh.scale);
    mesh.position.set(0, 0, 0);
    mesh.quaternion.set(0, 0, 0, 1);
    mesh.scale.set(1, 1, 1);
    mesh.updateMatrixWorld(true);
    if (this.world == null) {
      this.world = this._createWorld();
      this.setGravity(this.gravity);
    }
    this._initRigidBodies(rigidBodyParams);
    this._initConstraints(constraintParams);
    if (parent !== null)
      mesh.parent = parent;
    mesh.position.copy(currentPosition);
    mesh.quaternion.copy(currentQuaternion);
    mesh.scale.copy(currentScale);
    mesh.updateMatrixWorld(true);
    this.reset();
    this._updateRigidBodies();
    // Reduced internal init warmup from 30 to 5 to massively speed up loading time
    for (let k = 0; k < 5; k++) {
      this._stepSimulation(1 / 60);
      this._updateRigidBodies();
    }
    for (let i = 0; i < this.bodies.length; i++) {
      this.bodies[i].updateBone();
    }
    manager.freeThreeVector3(currentPosition);
    manager.freeThreeQuaternion(currentQuaternion);
    manager.freeThreeVector3(currentScale);
  }
  _initConstraints(constraints) {
    for (let i = 0, il = constraints.length; i < il; i++) {
      const params = constraints[i];
      const bodyA = this.bodies[params.rigidbodyIndexA];
      const bodyB = this.bodies[params.rigidbodyIndexB];
      if (bodyA === undefined || bodyA === null || bodyB === undefined || bodyB === null) {
        console.warn(`Constraint ${i} has undefined rigidbody: bodyA=${bodyA}, bodyB=${bodyB}`);
        continue;
      }
      this.constraints.push(new Constraint(this.mesh, this.world, bodyA, bodyB, params, this.manager));
    }
  }
  _initRigidBodies(rigidBodies) {
    for (let i = 0, il = rigidBodies.length; i < il; i++) {
      this.bodies.push(new RigidBody(
        this.mesh,
        this.world,
        rigidBodies[i],
        this.manager
      ));
    }
  }
  _stepSimulation(delta) {
    const unitStep = this.unitStep;
    let stepTime = delta;
    let maxStepNum = (delta / unitStep | 0) + 1;
    if (stepTime < unitStep) {
      stepTime = unitStep;
      maxStepNum = 1;
    }
    if (maxStepNum > this.maxStepNum) {
      maxStepNum = this.maxStepNum;
    }
    this.world.stepSimulation(stepTime, maxStepNum, unitStep);
  }
  _updateBones() {
    for (let i = 0, il = this.bodies.length; i < il; i++) {
      const body = this.bodies[i];
      if (body.params.physicsMode === 0 || body.params.boneIndex === -1) continue;
      body.updateBone();
    }
  }
  /**
   * Physics Edge-Case Heuristics (物理边界工况收尾)
   * 1. 微小防抖 (Near-Rest Damping)：吸纳残存的振荡推斥微力。
   * 2. 安全限速器 (Velocity Limiter)：强制拦截因高速穿模或快速舞蹈动作引发的“投石机满弓引爆效应”
   */
  _dampNearRestBodies() {
    if (!this._dampVec) {
      this._dampVec = new Ammo.btVector3(0, 0, 0);
    }
    const v = this._dampVec;

    // 定向阻尼参数：低速阈值 & 阻尼系数
    const linThresh = 0.05;
    const factor = 0.7;

    // 安全限速阈值 (上限设为相对安全的极限范围，超纲即“砍”)
    const maxLinSpd = 50.0;
    const maxAngSpd = 30.0;

    for (let i = 0, il = this.bodies.length; i < il; i++) {
      const body = this.bodies[i];
      if (body.params.physicsMode === 0) continue;
      const rb = body.body;
      const lv = rb.getLinearVelocity();
      const av = rb.getAngularVelocity();
      const linSpd = Math.sqrt(lv.x() * lv.x() + lv.y() * lv.y() + lv.z() * lv.z());
      const angSpd = Math.sqrt(av.x() * av.x() + av.y() * av.y() + av.z() * av.z());

      // 绝对限速拦截系统：将过载速度等比例归一化为最高限速
      if (linSpd > maxLinSpd) {
        const scale = maxLinSpd / linSpd;
        v.setValue(lv.x() * scale, lv.y() * scale, lv.z() * scale);
        rb.setLinearVelocity(v);
      }
      if (angSpd > maxAngSpd) {
        const scale = maxAngSpd / angSpd;
        v.setValue(av.x() * scale, av.y() * scale, av.z() * scale);
        rb.setAngularVelocity(v);
      }

      // 定向阻尼 v2：穿体修正版
      // 核心逻辑：穿体时用轻阻尼防止抖动，正常位置时轻微阻尼防止飞走
      if (linSpd < linThresh) {
        const tr = rb.getCenterOfMassTransform();
        const origin = tr.getOrigin();
        const boneWorldPos = body.bone.getWorldPosition(this.manager.allocThreeVector3());
        const dx = origin.x() - boneWorldPos.x;
        const dy = origin.y() - boneWorldPos.y;
        const dz = origin.z() - boneWorldPos.z;
        const distSq = dx * dx + dy * dy + dz * dz;
        const dot = lv.x() * dx + lv.y() * dy + lv.z() * dz;
        const HOLD_FACTOR = factor;
        const LIGHT_FACTOR = 0.85;
        let dampFactor = 1.0;
        if (distSq > 0.25) {
          dampFactor = LIGHT_FACTOR;
        } else if (dot > 0) {
          dampFactor = HOLD_FACTOR;
        }
        if (dampFactor < 1.0) {
          v.setValue(lv.x() * dampFactor, lv.y() * dampFactor, lv.z() * dampFactor);
          rb.setLinearVelocity(v);
        }
        this.manager.freeThreeVector3(boneWorldPos);
      }
    }
  }
  _updateRigidBodies() {
    for (let i = 0, il = this.bodies.length; i < il; i++) {
      this.bodies[i].updateFromBone();
    }
  }
  /**
   * Creates MMDPhysicsHelper
   */
  createHelper() {
    return new MMDPhysicsHelper(this.mesh, this);
  }
  /**
   * Resets rigid bodies transform to current bone's.
   */
  reset() {
    for (let i = 0, il = this.bodies.length; i < il; i++) {
      this.bodies[i].reset();
    }
    return this;
  }
  /**
   * Sets gravity.
   */
  setGravity(gravity) {
    if (!this._ammoGravity) this._ammoGravity = new Ammo.btVector3(0, 0, 0);
    this._ammoGravity.setValue(gravity.x, gravity.y, gravity.z);
    this.world.setGravity(this._ammoGravity);
    this.gravity.copy(gravity);
    return this;
  }
  /**
   * Advances Physics calculation and updates bones.
   */
  update(delta) {
    // Safety clamp: Cap delta to 0.1s to prevent Physics explosions during tab switching or lag
    if (delta > 0.1) delta = 0.1;

    const manager = this.manager;
    const mesh = this.mesh;
    // Capture previous-frame quaternion before any updates (used by rotation compensation + spin-lock)
    const prevMeshQuat = manager.allocThreeQuaternion();
    prevMeshQuat.copy(this._lastMeshQuat);
    let isNonDefaultScale = false;
    const position = manager.allocThreeVector3();
    const quaternion = manager.allocThreeQuaternion();
    const scale = manager.allocThreeVector3();
    // Ensure matrixWorld reflects latest mesh.quaternion/position before decompose
    mesh.updateMatrixWorld(true);
    mesh.matrixWorld.decompose(position, quaternion, scale);

    // [终极魔法 1: 局部相对重力 (Local Gravity)]
    // 修改引擎全局向下的重力，使其随模型旋转。无论角色躺下还是倒立，重力永远从头指向脚。
    // 这解除了衣服关节在非直立状态下被逼入死角导致“炸模”的设计缺陷。
    const localGravity = manager.allocThreeVector3();
    localGravity.copy(this.gravity).applyQuaternion(quaternion);
    if (!this._ammoGravity) this._ammoGravity = new Ammo.btVector3(0, 0, 0);
    this._ammoGravity.setValue(localGravity.x, localGravity.y, localGravity.z);
    this.world.setGravity(this._ammoGravity);
    manager.freeThreeVector3(localGravity);

    // [终极魔法 2: 局部专属地板 (Local Floor)]
    // 让隐形地板与角色位置和旋转完全同步结合。平躺时地板不会拦腰切断模型。
    if (this.groundBody) {
      const tr = manager.allocTransform();
      const ms = this.groundBody.getMotionState();
      if (ms) {
        ms.getWorldTransform(tr);
        const o = tr.getOrigin();
        o.setValue(position.x, position.y, position.z);
        const q = manager.allocQuaternion();
        q.setValue(quaternion.x, quaternion.y, quaternion.z, quaternion.w);
        tr.setRotation(q);
        this.groundBody.setCenterOfMassTransform(tr);
        ms.setWorldTransform(tr);
        manager.freeQuaternion(q);
      }
      manager.freeTransform(tr);
    }

    // World Position Compensation: Uncouple global dragging inertia from the physics simulation
    // This allows the model to be teleported across space without stretching dynamic constraints.
    if (delta > 0) {
      const currentPos = position;
      const displacement = manager.allocThreeVector3();
      displacement.subVectors(currentPos, this._lastMeshPos);

      if (displacement.lengthSq() > 0.000001) {
        const tr = manager.allocTransform();

        for (let i = 0, il = this.bodies.length; i < il; i++) {
          const body = this.bodies[i];
          // Compensate Mode 1 and 2 (Physics-driven) bones
          if (body.params.physicsMode !== 0 && body.body) {
            const ms = body.body.getMotionState();
            if (ms) {
              ms.getWorldTransform(tr);
              const o = tr.getOrigin();
              o.setValue(o.x() + displacement.x, o.y() + displacement.y, o.z() + displacement.z);
              body.body.setCenterOfMassTransform(tr);
              ms.setWorldTransform(tr);
            }
          }
        }
        manager.freeTransform(tr);
      }
      this._lastMeshPos.copy(currentPos);
      manager.freeThreeVector3(displacement);
    }

    // World Rotation Compensation: Sync dynamic rigid bodies when the model rotates.
    if (delta > 0) {
      const deltaQuat = manager.allocThreeQuaternion();
      deltaQuat.copy(prevMeshQuat).invert().multiply(quaternion);
      const rotAngle = 2 * Math.acos(Math.min(1, Math.abs(deltaQuat.w)));

      if (rotAngle > 0.001) {
        const pivot = position;
        const tr = manager.allocTransform();

        for (let i = 0, il = this.bodies.length; i < il; i++) {
          const body = this.bodies[i];
          if (body.params.physicsMode !== 0 && body.body) {
            const ms = body.body.getMotionState();
            if (ms) {
              ms.getWorldTransform(tr);
              const o = tr.getOrigin();

              // Rotate position around pivot
              const px = o.x() - pivot.x, py = o.y() - pivot.y, pz = o.z() - pivot.z;
              const rVec = manager.allocThreeVector3();
              rVec.set(px, py, pz).applyQuaternion(deltaQuat);
              o.setValue(rVec.x + pivot.x, rVec.y + pivot.y, rVec.z + pivot.z);
              manager.freeThreeVector3(rVec);

              // Rotate orientation
              const bodyQuat = tr.getRotation();
              const thQ = manager.allocThreeQuaternion();
              thQ.set(bodyQuat.x(), bodyQuat.y(), bodyQuat.z(), bodyQuat.w());
              thQ.premultiply(deltaQuat);
              const ammoQ = manager.allocQuaternion();
              ammoQ.setValue(thQ.x, thQ.y, thQ.z, thQ.w);
              tr.setRotation(ammoQ);
              manager.freeQuaternion(ammoQ);
              manager.freeThreeQuaternion(thQ);

              body.body.setCenterOfMassTransform(tr);
              ms.setWorldTransform(tr);

              // Rotate velocities to keep inertia direction consistent
              const lv = body.body.getLinearVelocity();
              const av = body.body.getAngularVelocity();
              const rvl = manager.allocThreeVector3();
              rvl.set(lv.x(), lv.y(), lv.z()).applyQuaternion(deltaQuat);
              const rva = manager.allocThreeVector3();
              rva.set(av.x(), av.y(), av.z()).applyQuaternion(deltaQuat);
              lv.setValue(rvl.x, rvl.y, rvl.z);
              av.setValue(rva.x, rva.y, rva.z);
              body.body.setLinearVelocity(lv);
              body.body.setAngularVelocity(av);
              manager.freeThreeVector3(rvl);
              manager.freeThreeVector3(rva);
            }
          }
        }
        manager.freeTransform(tr);
      }
      manager.freeThreeQuaternion(deltaQuat);
    }

    if (scale.x !== 1 || scale.y !== 1 || scale.z !== 1) {
      isNonDefaultScale = true;
    }
    let parent;
    if (isNonDefaultScale) {
      parent = mesh.parent;
      if (parent !== null)
        mesh.parent = null;
      scale.copy(this.mesh.scale);
      mesh.scale.set(1, 1, 1);
    }
    mesh.updateMatrixWorld(true);
    this._updateRigidBodies();

    // Spin-Lock Mechanism (自旋锁防撕裂机制):
    // Only trigger if rotation compensation did NOT already handle this frame's rotation.
    // Also guard against delta <= 0 to avoid Infinity/NaN.
    const angleDelta = prevMeshQuat.angleTo(quaternion);
    const angularVelocity = delta > 0 ? angleDelta / delta : 0;
    const rotationCompensated = delta > 0 && angleDelta > 0.001;
    if (!rotationCompensated && angularVelocity > 3.0) { // 阈值：> 3.0 rad/s (约 170 度/秒)
      const zeroVel = manager.allocVector3();
      zeroVel.setValue(0, 0, 0);
      for (let i = 0, il = this.bodies.length; i < il; i++) {
        const body = this.bodies[i];
        if (body.params.physicsMode !== 0 && body.body) {
          // 强制拽回到目前的骨架安全坐标上
          body._setTransformFromBone();
          // 清除上一帧累积的毁灭性离心冲量
          body.body.setLinearVelocity(zeroVel);
          body.body.setAngularVelocity(zeroVel);
          body.body.clearForces();
        }
      }
      manager.freeVector3(zeroVel);
    }
    this._lastMeshQuat.copy(quaternion);
    manager.freeThreeQuaternion(prevMeshQuat);

    this._stepSimulation(delta);
    this._dampNearRestBodies();
    this._updateBones();
    if (isNonDefaultScale) {
      if (parent != null)
        mesh.parent = parent;
      mesh.scale.copy(scale);
    }
    return this;
  }
  /**
   * Warm ups Rigid bodies. Calculates cycles steps.
   */
  warmup(cycles) {
    for (let i = 0; i < cycles; i++) {
      this.update(1 / 60);
    }
  }
}

const initAmmo = async () => Ammo.bind(Ammo)(Ammo);

const MMDAmmoPhysics = (mmd) => {
  const physics = new MMDPhysics(
    mmd.mesh,
    mmd.pmx.rigidBodies,
    mmd.pmx.joints
  );
  // Cut down external warmup from 60 to 10 frames to avoid completely freezing browser tab
  physics.warmup(10);
  return {
    createHelper: () => physics.createHelper(),
    reset: () => physics.reset(),
    update: (delta) => physics.update(delta),
    setGravity: (gravity) => physics.setGravity(gravity),
    getPhysics: () => physics
  };
};

export { MMDAmmoPhysics, initAmmo };
