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
    all.setValue(...params.rotationMin);
    aul.setValue(...params.rotationMax);
    constraint.setLinearLowerLimit(lll);
    constraint.setLinearUpperLimit(lul);
    constraint.setAngularLowerLimit(all);
    constraint.setAngularUpperLimit(aul);
    for (let i = 0; i < 3; i++) {
      if (params.springPosition[i] !== 0) {
        constraint.enableSpring(i, true);
        constraint.setStiffness(i, params.springPosition[i]);
        // Ammo.js 可能不会像 Bullet C++ 一样默认 m_springDamping=1.0，
        // 显式设置临界阻尼，防止低阻尼刚体（飘带）在弹簧力下永远振荡
        if (constraint.setDamping) constraint.setDamping(i, 1.0);
      }
    }
    for (let i = 0; i < 3; i++) {
      if (params.springRotation[i] !== 0) {
        constraint.enableSpring(i + 3, true);
        constraint.setStiffness(i + 3, params.springRotation[i]);
        if (constraint.setDamping) constraint.setDamping(i + 3, 1.0);
      }
    }
    if (constraint.setParam !== void 0) {
      for (let i = 0; i < 6; i++) {
        constraint.setParam(2, 0.475, i);
      }
    }
    this.world.addConstraint(constraint, true);
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
  constructor(mesh, world, params, manager) {
    this.mesh = mesh;
    this.world = world;
    this.params = params;
    this.manager = manager;
    const generateShape = (p) => {
      const [width, height, depth] = p.shapeSize;
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
    const shape = generateShape(this.params);
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
    info.set_m_friction(this.params.friction);
    info.set_m_restitution(this.params.repulsion);
    const body = new Ammo.btRigidBody(info);
    if (this.params.physicsMode === 0) {
      body.setCollisionFlags(body.getCollisionFlags() | 2);
    }
    body.setDamping(this.params.linearDamping, this.params.angularDamping);
    body.setSleepingThresholds(0, 0);
    this.world.addRigidBody(body, 1 << this.params.collisionGroup, this.params.collisionMask);
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
    const tr = manager.allocTransform();
    this.body.getMotionState().getWorldTransform(tr);
    manager.copyOrigin(tr, form);
    this.body.setWorldTransform(tr);
    manager.freeTransform(tr);
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
    this.bone.position.copy(thV);
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
    this.bone.quaternion.copy(thQ2.multiply(thQ3).normalize());
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
    if (this.params.physicsMode === 0 || this.params.boneIndex === -1) {
      return this;
    }
    this._updateBoneRotation();
    if (this.params.physicsMode === 1)
      this._updateBonePosition();
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
    this.manager = new ResourceManager();
    this.mesh = mesh;
    this.unitStep = params.unitStep !== void 0 ? params.unitStep : 1 / 65;
    this.maxStepNum = params.maxStepNum !== void 0 ? params.maxStepNum : 3;
    this.gravity = new Vector3(0, -9.8 * 10, 0);
    if (params.gravity !== void 0)
      this.gravity.copy(params.gravity);
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
    // Split Impulse: 将穿模位置修正与速度求解分离
    // 防止堆叠刚体（如缎带上叠加其他物理体）因 ERP 位置修正注入动量而产生振荡
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
      this.bodies[i].updateBone();
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
    this.world.setGravity(new Ammo.btVector3(gravity.x, gravity.y, gravity.z));
    this.gravity.copy(gravity);
    return this;
  }
  /**
   * Advances Physics calculation and updates bones.
   */
  update(delta) {
    const manager = this.manager;
    const mesh = this.mesh;
    let isNonDefaultScale = false;
    const position = manager.allocThreeVector3();
    const quaternion = manager.allocThreeQuaternion();
    const scale = manager.allocThreeVector3();
    mesh.matrixWorld.decompose(position, quaternion, scale);
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
    this._stepSimulation(delta);
    this._updateBones();
    if (isNonDefaultScale) {
      if (parent != null)
        mesh.parent = parent;
      mesh.scale.copy(scale);
    }
    manager.freeThreeVector3(scale);
    manager.freeThreeQuaternion(quaternion);
    manager.freeThreeVector3(position);
    return this;
  }
  /**
   * Warm ups Rigid bodies. Calculates cycles steps.
   */
  warmup(cycles) {
    for (let i = 0; i < cycles; i++) {
      this.update(1 / 60);
    }
    return this;
  }
}

const initAmmo = async () => Ammo.bind(Ammo)(Ammo);

const MMDAmmoPhysics = (mmd) => {
  const physics = new MMDPhysics(
    mmd.mesh,
    mmd.pmx.rigidBodies,
    mmd.pmx.joints
  );
  physics.warmup(60);
  return {
    createHelper: () => physics.createHelper(),
    reset: () => physics.reset(),
    update: (delta) => physics.update(delta),
    setGravity: (gravity) => physics.setGravity(gravity),
    getPhysics: () => physics
  };
};

export { MMDAmmoPhysics, initAmmo };
