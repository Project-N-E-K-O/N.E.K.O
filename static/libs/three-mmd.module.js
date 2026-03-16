import { AddOperation, AnimationClip, Bone, BufferAttribute, BufferGeometry, Color, CustomBlending, DefaultLoadingManager, DoubleSide, DstAlphaFactor, Euler, FileLoader, FrontSide, Interpolant, Loader, LoaderUtils, MultiplyOperation, NearestFilter, NumberKeyframeTrack, OneMinusSrcAlphaFactor, Quaternion, QuaternionKeyframeTrack, RGB_ETC1_Format, RGB_ETC2_Format, RGB_PVRTC_2BPPV1_Format, RGB_PVRTC_4BPPV1_Format, RGB_S3TC_DXT1_Format, RepeatWrapping, SRGBColorSpace, ShaderLib, ShaderMaterial, Skeleton, SkinnedMesh, SrcAlphaFactor, TangentSpaceNormalMap, TextureLoader, UniformsUtils, Vector3, VectorKeyframeTrack } from "three";
import { TGALoader } from "three/addons/loaders/TGALoader.js";

//#region ../../node_modules/.pnpm/babylon-mmd@1.0.0_@babylonjs+core@8.29.0/node_modules/babylon-mmd/esm/Loader/Parser/ILogger.js
/**
* A logger that outputs to the console
*
* generally, you can use this class as default logger
*/
var ConsoleLogger = class {
	log(message) {
		console.log(message);
	}
	warn(message) {
		console.warn(message);
	}
	error(message) {
		console.error(message);
	}
};

//#endregion
//#region ../../node_modules/.pnpm/babylon-mmd@1.0.0_@babylonjs+core@8.29.0/node_modules/babylon-mmd/esm/Loader/Parser/endianness.js
/**
* Endianness utility class for serlization/deserialization
*/
var Endianness = class {
	/**
	* Whether the device is little endian
	*/
	isDeviceLittleEndian;
	constructor() {
		this.isDeviceLittleEndian = this._getIsDeviceLittleEndian();
	}
	_getIsDeviceLittleEndian() {
		const array = new Int16Array([256]);
		return new Int8Array(array.buffer)[1] === 1;
	}
	/**
	* Changes the byte order of the array
	* @param array Array to swap
	*/
	swap16Array(array, offset = 0, length = array.length) {
		for (let i = offset; i < length; ++i) {
			const value = array[i];
			array[i] = (value & 255) << 8 | value >> 8 & 255;
		}
	}
	/**
	* Changes the byte order of the array
	* @param array Array to swap
	*/
	swap32Array(array, offset = 0, length = array.length) {
		for (let i = offset; i < length; ++i) {
			const value = array[i];
			array[i] = (value & 255) << 24 | (value & 65280) << 8 | value >> 8 & 65280 | value >> 24 & 255;
		}
	}
};

//#endregion
//#region ../../node_modules/.pnpm/babylon-mmd@1.0.0_@babylonjs+core@8.29.0/node_modules/babylon-mmd/esm/Loader/Parser/mmdDataDeserializer.js
/**
* DataView wrapper for deserializing MMD data
*/
var MmdDataDeserializer = class extends Endianness {
	_dataView;
	_decoder;
	_offset;
	/**
	* Creates MMD data deserializer
	* @param arrayBuffer ArrayBuffer to deserialize
	*/
	constructor(arrayBuffer) {
		super();
		this._dataView = new DataView(arrayBuffer);
		this._decoder = null;
		this._offset = 0;
	}
	/**
	* Current offset in the buffer
	*/
	get offset() {
		return this._offset;
	}
	set offset(value) {
		this._offset = value;
	}
	/**
	* Read a uint8 value
	* @returns Uint8 value
	*/
	getUint8() {
		const value = this._dataView.getUint8(this._offset);
		this._offset += 1;
		return value;
	}
	/**
	* Read a int8 value
	* @returns Int8 value
	*/
	getInt8() {
		const value = this._dataView.getInt8(this._offset);
		this._offset += 1;
		return value;
	}
	/**
	* Read a uint16 value
	* @returns Uint16 value
	*/
	getUint16() {
		const value = this._dataView.getUint16(this._offset, true);
		this._offset += 2;
		return value;
	}
	/**
	* Read a uint16 array
	* @param dest Destination array
	*/
	getUint16Array(dest) {
		const source = new Uint8Array(this._dataView.buffer, this._offset, dest.byteLength);
		new Uint8Array(dest.buffer, dest.byteOffset, dest.byteLength).set(source);
		this._offset += dest.byteLength;
		if (!this.isDeviceLittleEndian) this.swap16Array(dest);
	}
	/**
	* Read a int16 value
	* @returns Int16 value
	*/
	getInt16() {
		const value = this._dataView.getInt16(this._offset, true);
		this._offset += 2;
		return value;
	}
	/**
	* Read a uint32 value
	* @returns Uint32 value
	*/
	getUint32() {
		const value = this._dataView.getUint32(this._offset, true);
		this._offset += 4;
		return value;
	}
	/**
	* Read a int32 value
	* @returns Int32 value
	*/
	getInt32() {
		const value = this._dataView.getInt32(this._offset, true);
		this._offset += 4;
		return value;
	}
	/**
	* Read a float32 value
	* @returns Float32 value
	*/
	getFloat32() {
		const value = this._dataView.getFloat32(this._offset, true);
		this._offset += 4;
		return value;
	}
	/**
	* Read a float32 tuple
	* @param length Tuple length
	* @returns Float32 tuple
	*/
	getFloat32Tuple(length) {
		const result = new Array(length);
		for (let i = 0; i < length; ++i) {
			result[i] = this._dataView.getFloat32(this._offset, true);
			this._offset += 4;
		}
		return result;
	}
	/**
	* Initializes TextDecoder with the specified encoding
	* @param encoding Encoding
	*/
	initializeTextDecoder(encoding) {
		this._decoder = new TextDecoder(encoding);
	}
	/**
	* Decode the string in the encoding determined by the initializeTextDecoder method
	* @param length Length of the string in bytes
	* @param trim Whether to trim the string, usally used in Shift-JIS encoding
	* @returns Decoded string
	*/
	getDecoderString(length, trim) {
		if (this._decoder === null) throw new Error("TextDecoder is not initialized.");
		let bytes = new Uint8Array(this._dataView.buffer, this._offset, length);
		this._offset += length;
		if (trim) {
			for (let i = 0; i < bytes.length; ++i) if (bytes[i] === 0) {
				bytes = bytes.subarray(0, i);
				break;
			}
		}
		return this._decoder.decode(bytes);
	}
	/**
	* Read a utf-8 string
	* @param length Length of the string in bytes
	* @returns Utf-8 string
	*/
	getSignatureString(length) {
		const decoder = new TextDecoder("utf-8");
		const bytes = new Uint8Array(this._dataView.buffer, this._offset, length);
		this._offset += length;
		return decoder.decode(bytes);
	}
	/**
	* The number of bytes available
	*/
	get bytesAvailable() {
		return this._dataView.byteLength - this._offset;
	}
};

//#endregion
//#region ../../node_modules/.pnpm/babylon-mmd@1.0.0_@babylonjs+core@8.29.0/node_modules/babylon-mmd/esm/Loader/Parser/pmdObject.js
/**
* Pmd object for temporal use in pmd parser
* @internal
*/
var PmdObject;
(function(PmdObject$1) {
	(function(Bone$1) {
		(function(Type) {
			Type[Type["Rotate"] = 0] = "Rotate";
			Type[Type["RotateMove"] = 1] = "RotateMove";
			Type[Type["Ik"] = 2] = "Ik";
			Type[Type["Unknown"] = 3] = "Unknown";
			Type[Type["IkLink"] = 4] = "IkLink";
			Type[Type["RotateEffect"] = 5] = "RotateEffect";
			Type[Type["IkTo"] = 6] = "IkTo";
			Type[Type["Invisible"] = 7] = "Invisible";
			Type[Type["Twist"] = 8] = "Twist";
			Type[Type["RotateRatio"] = 9] = "RotateRatio";
		})(Bone$1.Type || (Bone$1.Type = {}));
	})(PmdObject$1.Bone || (PmdObject$1.Bone = {}));
})(PmdObject || (PmdObject = {}));

//#endregion
//#region ../../node_modules/.pnpm/babylon-mmd@1.0.0_@babylonjs+core@8.29.0/node_modules/babylon-mmd/esm/Loader/Parser/pmxObject.js
var PmxObject;
(function(PmxObject$1) {
	(function(Header) {
		(function(Encoding) {
			Encoding[Encoding["Utf16le"] = 0] = "Utf16le";
			Encoding[Encoding["Utf8"] = 1] = "Utf8";
			Encoding[Encoding["ShiftJis"] = 2] = "ShiftJis";
		})(Header.Encoding || (Header.Encoding = {}));
	})(PmxObject$1.Header || (PmxObject$1.Header = {}));
	(function(Vertex) {
		(function(BoneWeightType) {
			BoneWeightType[BoneWeightType["Bdef1"] = 0] = "Bdef1";
			BoneWeightType[BoneWeightType["Bdef2"] = 1] = "Bdef2";
			BoneWeightType[BoneWeightType["Bdef4"] = 2] = "Bdef4";
			BoneWeightType[BoneWeightType["Sdef"] = 3] = "Sdef";
			BoneWeightType[BoneWeightType["Qdef"] = 4] = "Qdef";
		})(Vertex.BoneWeightType || (Vertex.BoneWeightType = {}));
	})(PmxObject$1.Vertex || (PmxObject$1.Vertex = {}));
	(function(Material) {
		(function(Flag) {
			Flag[Flag["IsDoubleSided"] = 1] = "IsDoubleSided";
			Flag[Flag["EnabledGroundShadow"] = 2] = "EnabledGroundShadow";
			Flag[Flag["EnabledDrawShadow"] = 4] = "EnabledDrawShadow";
			Flag[Flag["EnabledReceiveShadow"] = 8] = "EnabledReceiveShadow";
			Flag[Flag["EnabledToonEdge"] = 16] = "EnabledToonEdge";
			Flag[Flag["EnabledVertexColor"] = 32] = "EnabledVertexColor";
			Flag[Flag["EnabledPointDraw"] = 64] = "EnabledPointDraw";
			Flag[Flag["EnabledLineDraw"] = 128] = "EnabledLineDraw";
		})(Material.Flag || (Material.Flag = {}));
		(function(SphereTextureMode) {
			SphereTextureMode[SphereTextureMode["Off"] = 0] = "Off";
			SphereTextureMode[SphereTextureMode["Multiply"] = 1] = "Multiply";
			SphereTextureMode[SphereTextureMode["Add"] = 2] = "Add";
			SphereTextureMode[SphereTextureMode["SubTexture"] = 3] = "SubTexture";
		})(Material.SphereTextureMode || (Material.SphereTextureMode = {}));
	})(PmxObject$1.Material || (PmxObject$1.Material = {}));
	(function(Bone$1) {
		(function(Flag) {
			Flag[Flag["UseBoneIndexAsTailPosition"] = 1] = "UseBoneIndexAsTailPosition";
			Flag[Flag["IsRotatable"] = 2] = "IsRotatable";
			Flag[Flag["IsMovable"] = 4] = "IsMovable";
			Flag[Flag["IsVisible"] = 8] = "IsVisible";
			Flag[Flag["IsControllable"] = 16] = "IsControllable";
			Flag[Flag["IsIkEnabled"] = 32] = "IsIkEnabled";
			/**
			* Whether to apply Append transform in a chain
			*
			* If this bit is 0, then in a bone structure with chain-append transform applied
			*
			* the append transform works by adding itself to each other's calculation results
			*/
			Flag[Flag["LocalAppendTransform"] = 128] = "LocalAppendTransform";
			/**
			* Whether to apply Append transform to rotation
			*/
			Flag[Flag["HasAppendRotate"] = 256] = "HasAppendRotate";
			/**
			* Whether to apply Append transform to position
			*/
			Flag[Flag["HasAppendMove"] = 512] = "HasAppendMove";
			Flag[Flag["HasAxisLimit"] = 1024] = "HasAxisLimit";
			Flag[Flag["HasLocalVector"] = 2048] = "HasLocalVector";
			/**
			* Whether to apply transform after physics
			*
			* If this bit is 1, the bone transform is applied after physics
			*/
			Flag[Flag["TransformAfterPhysics"] = 4096] = "TransformAfterPhysics";
			Flag[Flag["IsExternalParentTransformed"] = 8192] = "IsExternalParentTransformed";
		})(Bone$1.Flag || (Bone$1.Flag = {}));
	})(PmxObject$1.Bone || (PmxObject$1.Bone = {}));
	(function(Morph) {
		(function(Category) {
			Category[Category["System"] = 0] = "System";
			Category[Category["Eyebrow"] = 1] = "Eyebrow";
			Category[Category["Eye"] = 2] = "Eye";
			Category[Category["Lip"] = 3] = "Lip";
			Category[Category["Other"] = 4] = "Other";
		})(Morph.Category || (Morph.Category = {}));
		(function(Type) {
			Type[Type["GroupMorph"] = 0] = "GroupMorph";
			Type[Type["VertexMorph"] = 1] = "VertexMorph";
			Type[Type["BoneMorph"] = 2] = "BoneMorph";
			Type[Type["UvMorph"] = 3] = "UvMorph";
			Type[Type["AdditionalUvMorph1"] = 4] = "AdditionalUvMorph1";
			Type[Type["AdditionalUvMorph2"] = 5] = "AdditionalUvMorph2";
			Type[Type["AdditionalUvMorph3"] = 6] = "AdditionalUvMorph3";
			Type[Type["AdditionalUvMorph4"] = 7] = "AdditionalUvMorph4";
			Type[Type["MaterialMorph"] = 8] = "MaterialMorph";
			Type[Type["FlipMorph"] = 9] = "FlipMorph";
			Type[Type["ImpulseMorph"] = 10] = "ImpulseMorph";
		})(Morph.Type || (Morph.Type = {}));
		(function(MaterialMorph) {
			(function(Type) {
				Type[Type["Multiply"] = 0] = "Multiply";
				Type[Type["Add"] = 1] = "Add";
			})(MaterialMorph.Type || (MaterialMorph.Type = {}));
		})(Morph.MaterialMorph || (Morph.MaterialMorph = {}));
	})(PmxObject$1.Morph || (PmxObject$1.Morph = {}));
	(function(DisplayFrame) {
		(function(FrameData) {
			(function(FrameType) {
				FrameType[FrameType["Bone"] = 0] = "Bone";
				FrameType[FrameType["Morph"] = 1] = "Morph";
			})(FrameData.FrameType || (FrameData.FrameType = {}));
		})(DisplayFrame.FrameData || (DisplayFrame.FrameData = {}));
	})(PmxObject$1.DisplayFrame || (PmxObject$1.DisplayFrame = {}));
	(function(RigidBody) {
		(function(ShapeType) {
			ShapeType[ShapeType["Sphere"] = 0] = "Sphere";
			ShapeType[ShapeType["Box"] = 1] = "Box";
			ShapeType[ShapeType["Capsule"] = 2] = "Capsule";
		})(RigidBody.ShapeType || (RigidBody.ShapeType = {}));
		(function(PhysicsMode) {
			PhysicsMode[PhysicsMode["FollowBone"] = 0] = "FollowBone";
			PhysicsMode[PhysicsMode["Physics"] = 1] = "Physics";
			PhysicsMode[PhysicsMode["PhysicsWithBone"] = 2] = "PhysicsWithBone";
		})(RigidBody.PhysicsMode || (RigidBody.PhysicsMode = {}));
	})(PmxObject$1.RigidBody || (PmxObject$1.RigidBody = {}));
	(function(Joint) {
		(function(Type) {
			Type[Type["Spring6dof"] = 0] = "Spring6dof";
			Type[Type["Sixdof"] = 1] = "Sixdof";
			Type[Type["P2p"] = 2] = "P2p";
			Type[Type["ConeTwist"] = 3] = "ConeTwist";
			Type[Type["Slider"] = 4] = "Slider";
			Type[Type["Hinge"] = 5] = "Hinge";
		})(Joint.Type || (Joint.Type = {}));
	})(PmxObject$1.Joint || (PmxObject$1.Joint = {}));
	(function(SoftBody) {
		(function(Type) {
			Type[Type["TriMesh"] = 0] = "TriMesh";
			Type[Type["Rope"] = 1] = "Rope";
		})(SoftBody.Type || (SoftBody.Type = {}));
		(function(Flag) {
			Flag[Flag["Blink"] = 1] = "Blink";
			Flag[Flag["ClusterCreation"] = 2] = "ClusterCreation";
			Flag[Flag["LinkCrossing"] = 4] = "LinkCrossing";
		})(SoftBody.Flag || (SoftBody.Flag = {}));
		(function(AeroDynamicModel) {
			AeroDynamicModel[AeroDynamicModel["VertexPoint"] = 0] = "VertexPoint";
			AeroDynamicModel[AeroDynamicModel["VertexTwoSided"] = 1] = "VertexTwoSided";
			AeroDynamicModel[AeroDynamicModel["VertexOneSided"] = 2] = "VertexOneSided";
			AeroDynamicModel[AeroDynamicModel["FaceTwoSided"] = 3] = "FaceTwoSided";
			AeroDynamicModel[AeroDynamicModel["FaceOneSided"] = 4] = "FaceOneSided";
		})(SoftBody.AeroDynamicModel || (SoftBody.AeroDynamicModel = {}));
	})(PmxObject$1.SoftBody || (PmxObject$1.SoftBody = {}));
})(PmxObject || (PmxObject = {}));

//#endregion
//#region ../../node_modules/.pnpm/babylon-mmd@1.0.0_@babylonjs+core@8.29.0/node_modules/babylon-mmd/esm/Loader/Parser/pmdReader.js
/**
* PmdReader is a static class that parses PMD data
*/
var PmdReader = class {
	constructor() {}
	/**
	* Parses PMD data asynchronously
	* @param data Arraybuffer of PMD data
	* @param logger Logger
	* @returns PMD data as a PmxObject
	* @throws {Error} If the parse fails
	*/
	static async ParseAsync(data, logger = new ConsoleLogger()) {
		const dataDeserializer = new MmdDataDeserializer(data);
		dataDeserializer.initializeTextDecoder("shift-jis");
		const header = this._ParseHeader(dataDeserializer);
		const vertices = await this._ParseVerticesAsync(dataDeserializer);
		const indices = this._ParseIndices(dataDeserializer);
		const partialMaterials = this._ParseMaterials(dataDeserializer);
		const bones = this._ParseBones(dataDeserializer);
		const iks = this._ParseIks(dataDeserializer);
		const morphs = this._ParseMorphs(dataDeserializer);
		const [displayFrames, boneFrameStartIndex] = this._ParseDisplayFrames(dataDeserializer, morphs);
		if (dataDeserializer.bytesAvailable === 0) {
			const textures$1 = [];
			return {
				header,
				vertices,
				indices,
				textures: textures$1,
				materials: this._ConvertMaterials(partialMaterials, textures$1),
				bones: this._ConvertBones(bones, iks, vertices, displayFrames),
				morphs,
				displayFrames,
				rigidBodies: [],
				joints: [],
				softBodies: []
			};
		}
		if (dataDeserializer.getUint8() !== 0) this._ParseEnglishNames(dataDeserializer, header, bones, morphs, displayFrames, boneFrameStartIndex);
		const textures = this._ParseToonTextures(dataDeserializer);
		const materials = this._ConvertMaterials(partialMaterials, textures);
		if (dataDeserializer.bytesAvailable === 0) return {
			header,
			vertices,
			indices,
			textures,
			materials,
			bones: this._ConvertBones(bones, iks, vertices, displayFrames),
			morphs,
			displayFrames,
			rigidBodies: [],
			joints: [],
			softBodies: []
		};
		const rigidBodies = this._ParseRigidBodies(dataDeserializer);
		const finalBones = this._ConvertBones(bones, iks, vertices, displayFrames, rigidBodies);
		this._NormalizeRigidBodyPositions(rigidBodies, finalBones);
		const joints = this._ParseJoints(dataDeserializer);
		if (dataDeserializer.bytesAvailable > 0) logger.warn(`There are ${dataDeserializer.bytesAvailable} bytes left after parsing`);
		return {
			header,
			vertices,
			indices,
			textures,
			materials,
			bones: finalBones,
			morphs,
			displayFrames,
			rigidBodies,
			joints,
			softBodies: []
		};
	}
	static _ParseHeader(dataDeserializer) {
		if (dataDeserializer.bytesAvailable < 7) throw new Error("is not pmd file");
		const signature = dataDeserializer.getSignatureString(3);
		if (signature !== "Pmd") throw new Error("is not pmd file");
		const version = dataDeserializer.getFloat32();
		const modelName = dataDeserializer.getDecoderString(20, true);
		const comment = dataDeserializer.getDecoderString(256, true);
		return {
			signature,
			version,
			encoding: PmxObject.Header.Encoding.ShiftJis,
			additionalVec4Count: 0,
			vertexIndexSize: 2,
			textureIndexSize: 4,
			materialIndexSize: 4,
			boneIndexSize: 2,
			morphIndexSize: 2,
			rigidBodyIndexSize: 4,
			modelName,
			englishModelName: "",
			comment,
			englishComment: ""
		};
	}
	static async _ParseVerticesAsync(dataDeserializer) {
		const verticesCount = dataDeserializer.getUint32();
		const vertices = [];
		let time = performance.now();
		for (let i = 0; i < verticesCount; ++i) {
			const position = dataDeserializer.getFloat32Tuple(3);
			const normal = dataDeserializer.getFloat32Tuple(3);
			const uv = dataDeserializer.getFloat32Tuple(2);
			const weightType = PmxObject.Vertex.BoneWeightType.Bdef2;
			const boneWeight = {
				boneIndices: [dataDeserializer.getUint16(), dataDeserializer.getUint16()],
				boneWeights: dataDeserializer.getUint8() / 100
			};
			const edgeFlag = dataDeserializer.getUint8() !== 0;
			vertices.push({
				position,
				normal,
				uv,
				additionalVec4: [],
				weightType,
				boneWeight,
				edgeScale: edgeFlag ? 1 : 0
			});
			if (i % 1e4 === 0 && 100 < performance.now() - time) {
				await new Promise((resolve) => setTimeout(resolve, 0));
				time = performance.now();
			}
		}
		return vertices;
	}
	static _ParseIndices(dataDeserializer) {
		const indicesCount = dataDeserializer.getUint32();
		const indices = new Uint16Array(indicesCount);
		dataDeserializer.getUint16Array(indices);
		return indices;
	}
	static _ParseMaterials(dataDeserializer) {
		const materialsCount = dataDeserializer.getUint32();
		const materials = [];
		for (let i = 0; i < materialsCount; ++i) {
			const diffuse = dataDeserializer.getFloat32Tuple(4);
			const shininess = dataDeserializer.getFloat32();
			const specular = dataDeserializer.getFloat32Tuple(3);
			const ambient = dataDeserializer.getFloat32Tuple(3);
			const toonTextureIndex = dataDeserializer.getInt8();
			const edgeFlag = dataDeserializer.getUint8();
			const indexCount = dataDeserializer.getUint32();
			const texturePath = dataDeserializer.getDecoderString(20, true);
			let flag = 0;
			if (edgeFlag !== 0) flag |= PmxObject.Material.Flag.EnabledToonEdge | PmxObject.Material.Flag.EnabledGroundShadow;
			if (diffuse[3] !== .98) flag |= PmxObject.Material.Flag.EnabledDrawShadow | PmxObject.Material.Flag.EnabledReceiveShadow;
			if (diffuse[3] < 1) flag |= PmxObject.Material.Flag.IsDoubleSided;
			let sphereTextureMode = PmxObject.Material.SphereTextureMode.Off;
			let diffuseTexturePath = "";
			let sphereTexturePath = "";
			{
				const paths = texturePath.split("*");
				for (let i$1 = 0; i$1 < paths.length; ++i$1) {
					const path = paths[i$1];
					let mode = PmxObject.Material.SphereTextureMode.Off;
					if (path !== "") {
						const extensionIndex = path.lastIndexOf(".");
						const extension = extensionIndex !== -1 ? path.substring(extensionIndex).toLowerCase() : "";
						if (extension === ".sph") mode = PmxObject.Material.SphereTextureMode.Multiply;
						else if (extension === ".spa") mode = PmxObject.Material.SphereTextureMode.Add;
					}
					if (mode !== PmxObject.Material.SphereTextureMode.Off) {
						sphereTextureMode = mode;
						sphereTexturePath = path;
					} else diffuseTexturePath = path;
				}
			}
			const material = {
				name: texturePath,
				englishName: "",
				diffuse,
				specular,
				shininess,
				ambient,
				flag,
				edgeColor: [
					0,
					0,
					0,
					1
				],
				edgeSize: 1,
				textureIndex: diffuseTexturePath,
				sphereTextureIndex: sphereTexturePath,
				sphereTextureMode,
				isSharedToonTexture: false,
				toonTextureIndex,
				comment: "",
				indexCount
			};
			materials.push(material);
		}
		return materials;
	}
	static _ParseBones(dataDeserializer) {
		const bonesCount = dataDeserializer.getUint16();
		const bones = [];
		for (let i = 0; i < bonesCount; ++i) {
			const bone = {
				name: dataDeserializer.getDecoderString(20, true),
				englishName: "",
				parentBoneIndex: dataDeserializer.getInt16(),
				tailIndex: dataDeserializer.getInt16(),
				type: dataDeserializer.getUint8(),
				ikIndex: dataDeserializer.getInt16(),
				position: dataDeserializer.getFloat32Tuple(3)
			};
			bones.push(bone);
		}
		return bones;
	}
	static _ParseIks(dataDeserializer) {
		const iksCount = dataDeserializer.getUint16();
		const iks = [];
		for (let i = 0; i < iksCount; ++i) {
			const boneIndex = dataDeserializer.getUint16();
			const targetIndex = dataDeserializer.getUint16();
			const ikLinkCount = dataDeserializer.getUint8();
			const iteration = dataDeserializer.getUint16();
			const rotationConstraint = dataDeserializer.getFloat32();
			const links = [];
			for (let j = 0; j < ikLinkCount; ++j) links.push(dataDeserializer.getUint16());
			const ik = {
				boneIndex,
				targetIndex,
				iteration,
				rotationConstraint,
				links
			};
			iks.push(ik);
		}
		return iks;
	}
	static _ParseMorphs(dataDeserializer) {
		const morphsCount = dataDeserializer.getUint16();
		if (morphsCount === 0) return [];
		const morphs = [];
		for (let i = 0; i < morphsCount; ++i) {
			const name = dataDeserializer.getDecoderString(20, true);
			const morphOffsetCount = dataDeserializer.getUint32();
			let morph = {
				name,
				englishName: "",
				category: dataDeserializer.getUint8(),
				type: PmxObject.Morph.Type.VertexMorph
			};
			const indices = new Int32Array(morphOffsetCount);
			const positions = new Float32Array(morphOffsetCount * 3);
			for (let i$1 = 0; i$1 < morphOffsetCount; ++i$1) {
				indices[i$1] = dataDeserializer.getUint32();
				positions[i$1 * 3 + 0] = dataDeserializer.getFloat32();
				positions[i$1 * 3 + 1] = dataDeserializer.getFloat32();
				positions[i$1 * 3 + 2] = dataDeserializer.getFloat32();
			}
			morph = {
				...morph,
				indices,
				positions
			};
			morphs.push(morph);
		}
		const baseSkinIndices = morphs.shift().indices;
		for (let i = 0; i < morphs.length; ++i) {
			const indices = morphs[i].indices;
			for (let j = 0; j < indices.length; ++j) {
				const indexKey = indices[j];
				if (0 <= indexKey && indexKey < baseSkinIndices.length) indices[j] = baseSkinIndices[indexKey];
				else indices[j] = 0;
			}
		}
		return morphs;
	}
	static _ParseDisplayFrames(dataDeserializer, morphs) {
		const displayFrames = [];
		const morphDisplayFramesCount = dataDeserializer.getUint8();
		for (let i = 0; i < morphDisplayFramesCount; ++i) {
			const frame = {
				type: PmxObject.DisplayFrame.FrameData.FrameType.Morph,
				index: dataDeserializer.getUint16()
			};
			const displayFrame = {
				name: morphs[frame.index]?.name ?? "",
				englishName: "",
				isSpecialFrame: true,
				frames: [frame]
			};
			displayFrames.push(displayFrame);
		}
		const boneFrameStartIndex = displayFrames.length;
		const boneDisplayFramesCount = dataDeserializer.getUint8();
		for (let i = 0; i < boneDisplayFramesCount; ++i) {
			const boneDisplayFrame = {
				name: dataDeserializer.getDecoderString(50, true),
				englishName: "",
				isSpecialFrame: false,
				frames: void 0
			};
			displayFrames.push(boneDisplayFrame);
		}
		const frameBoneIndicesCount = dataDeserializer.getUint32();
		for (let i = 0; i < frameBoneIndicesCount; ++i) {
			const boneIndex = dataDeserializer.getUint16();
			const displayFrame = displayFrames[boneFrameStartIndex + dataDeserializer.getUint8() - 1];
			if (displayFrame !== void 0) {
				const frame = {
					type: PmxObject.DisplayFrame.FrameData.FrameType.Bone,
					index: boneIndex
				};
				if (displayFrame.frames === void 0) displayFrame.frames = [frame];
				else displayFrame.frames.push(frame);
			}
		}
		for (let i = boneFrameStartIndex; i < displayFrames.length; ++i) {
			const displayFrame = displayFrames[i];
			if (displayFrame.frames === void 0) displayFrame.frames = [];
		}
		return [displayFrames, boneFrameStartIndex];
	}
	static _ParseEnglishNames(dataDeserializer, header, bones, morphs, displayFrames, boneFrameStartIndex) {
		header.englishModelName = dataDeserializer.getDecoderString(20, true);
		header.englishComment = dataDeserializer.getDecoderString(256, true);
		for (let i = 0; i < bones.length; ++i) bones[i].englishName = dataDeserializer.getDecoderString(20, true);
		for (let i = 0; i < morphs.length; ++i) morphs[i].englishName = dataDeserializer.getDecoderString(20, true);
		for (let i = boneFrameStartIndex; i < displayFrames.length; ++i) displayFrames[i].englishName = dataDeserializer.getDecoderString(50, true);
	}
	static _ParseToonTextures(dataDeserializer) {
		const textures = [];
		for (let i = 0; i < 10; ++i) textures.push(dataDeserializer.getDecoderString(100, true));
		return textures;
	}
	static _PathNormalize(path) {
		path = path.replace(/\\/g, "/");
		const pathArray = path.split("/");
		const resultArray = [];
		for (let i = 0; i < pathArray.length; ++i) {
			const pathElement = pathArray[i];
			if (pathElement === ".") continue;
			else if (pathElement === "..") resultArray.pop();
			else resultArray.push(pathElement);
		}
		return resultArray.join("/").toLowerCase();
	}
	static _ConvertMaterials(materials, textures) {
		const normalizedTextures = new Array(textures.length);
		for (let i = 0; i < textures.length; ++i) normalizedTextures[i] = this._PathNormalize(textures[i]);
		for (let i = 0; i < materials.length; ++i) {
			const material = materials[i];
			if (0 <= material.toonTextureIndex && material.toonTextureIndex < textures.length) {
				const normalizedToonTexturePath = normalizedTextures[material.toonTextureIndex];
				if (/toon(10|0[0-9])\.bmp/.test(normalizedToonTexturePath)) {
					material.isSharedToonTexture = true;
					let toonTextureIndex = normalizedToonTexturePath.substring(normalizedToonTexturePath.length - 6, normalizedToonTexturePath.length - 4);
					if (toonTextureIndex[0] === "n") toonTextureIndex = toonTextureIndex[1];
					material.toonTextureIndex = parseInt(toonTextureIndex, 10) - 1;
				}
			}
		}
		const textureIndexMap = /* @__PURE__ */ new Map();
		for (let i = 0; i < textures.length; ++i) textureIndexMap.set(this._PathNormalize(textures[i]), i);
		for (let i = 0; i < materials.length; ++i) {
			const material = materials[i];
			if (material.textureIndex !== "") {
				const normalizedDiffuseTexturePath = this._PathNormalize(material.textureIndex);
				let diffuseTextureIndex = textureIndexMap.get(normalizedDiffuseTexturePath);
				if (diffuseTextureIndex === void 0) {
					diffuseTextureIndex = textureIndexMap.size;
					textureIndexMap.set(normalizedDiffuseTexturePath, diffuseTextureIndex);
					textures.push(material.textureIndex);
				}
				material.textureIndex = diffuseTextureIndex;
			} else material.textureIndex = -1;
			if (material.sphereTextureIndex !== "") {
				const normalizedSphereTexturePath = this._PathNormalize(material.sphereTextureIndex);
				let sphereTextureIndex = textureIndexMap.get(normalizedSphereTexturePath);
				if (sphereTextureIndex === void 0) {
					sphereTextureIndex = textureIndexMap.size;
					textureIndexMap.set(normalizedSphereTexturePath, sphereTextureIndex);
					textures.push(material.sphereTextureIndex);
				}
				material.sphereTextureIndex = sphereTextureIndex;
			} else material.sphereTextureIndex = -1;
		}
		return materials;
	}
	/**
	* from pmx editor IK制限角.txt
	* format: minX, maxX, minY, maxY, minZ, maxZ
	*
	* 左ひざ,-180.0,-0.5,0.0,0.0,0.0,0.0
	* 右ひざ,-180.0,-0.5,0.0,0.0,0.0,0.0
	*/
	static _IkAngleLimitTable = new Map(Object.entries({
		"左ひざ": [
			-180,
			-.5,
			0,
			0,
			0,
			0
		],
		"右ひざ": [
			-180,
			-.5,
			0,
			0,
			0,
			0
		]
	}));
	static _ConvertBones(bones, iks, vertices, displayFrames, rigidBodies) {
		const ikMap = /* @__PURE__ */ new Map();
		for (let i = 0; i < iks.length; ++i) {
			const ikBoneIndex = iks[i].boneIndex;
			if (0 <= ikBoneIndex && ikBoneIndex < bones.length && !ikMap.has(ikBoneIndex)) ikMap.set(ikBoneIndex, i);
		}
		const finalBones = [];
		for (let i = 0; i < bones.length; ++i) {
			const bone = bones[i];
			const pmxBone = {
				name: bone.name,
				englishName: bone.englishName,
				position: bone.position,
				parentBoneIndex: bone.parentBoneIndex,
				transformOrder: 0,
				flag: PmxObject.Bone.Flag.UseBoneIndexAsTailPosition,
				tailPosition: bone.tailIndex <= 0 ? -1 : bone.tailIndex,
				appendTransform: void 0,
				axisLimit: void 0,
				localVector: void 0,
				externalParentTransform: void 0,
				ik: void 0
			};
			let isIkBone = ikMap.has(i);
			pmxBone.flag |= PmxObject.Bone.Flag.IsRotatable | PmxObject.Bone.Flag.IsVisible | PmxObject.Bone.Flag.IsControllable;
			pmxBone.flag &= ~PmxObject.Bone.Flag.IsMovable & ~PmxObject.Bone.Flag.IsIkEnabled & ~PmxObject.Bone.Flag.HasAppendRotate & ~PmxObject.Bone.Flag.HasAxisLimit;
			switch (bone.type) {
				case PmdObject.Bone.Type.RotateMove:
					pmxBone.flag |= PmxObject.Bone.Flag.IsMovable;
					break;
				case PmdObject.Bone.Type.Ik:
					isIkBone = true;
					break;
				case PmdObject.Bone.Type.RotateEffect:
					pmxBone.flag |= PmxObject.Bone.Flag.HasAppendRotate;
					pmxBone.flag &= ~PmxObject.Bone.Flag.UseBoneIndexAsTailPosition & ~PmxObject.Bone.Flag.IsVisible;
					pmxBone.appendTransform = {
						parentIndex: bone.tailIndex,
						ratio: bone.ikIndex * .01
					};
					break;
			}
			if (isIkBone) {
				pmxBone.flag |= PmxObject.Bone.Flag.IsMovable | PmxObject.Bone.Flag.IsIkEnabled;
				pmxBone.transformOrder = 1;
			}
			finalBones.push(pmxBone);
		}
		let boneCount = Math.min(finalBones.length, bones.length);
		for (let i = 0; i < boneCount; ++i) {
			const bone = bones[i];
			const pmxBone = finalBones[i];
			if (bone.type === PmdObject.Bone.Type.Twist) {
				let tailBone = bones[bone.tailIndex];
				if (tailBone === void 0) tailBone = bones[0];
				const tailBonePosition = tailBone.position;
				const bonePosition = bone.position;
				pmxBone.axisLimit = [
					tailBonePosition[0] - bonePosition[0],
					tailBonePosition[1] - bonePosition[1],
					tailBonePosition[2] - bonePosition[2]
				];
				const axisLimit = pmxBone.axisLimit;
				const length = Math.sqrt(axisLimit[0] * axisLimit[0] + axisLimit[1] * axisLimit[1] + axisLimit[2] * axisLimit[2]);
				axisLimit[0] /= length;
				axisLimit[1] /= length;
				axisLimit[2] /= length;
				pmxBone.flag &= ~PmxObject.Bone.Flag.UseBoneIndexAsTailPosition;
			}
		}
		const ikChainBones = [];
		for (let boneIndex = 0; boneIndex < boneCount; ++boneIndex) {
			const pmxBone = finalBones[boneIndex];
			if ((pmxBone.flag & PmxObject.Bone.Flag.IsIkEnabled) === 0) continue;
			let ikCount = 0;
			for (let ikIndex = 0; ikIndex < iks.length; ++ikIndex) {
				const ik = iks[ikIndex];
				if (ik.boneIndex !== boneIndex) continue;
				let pmxChainBone;
				if (ikCount === 0) {
					pmxChainBone = pmxBone;
					ikCount += 1;
				} else {
					pmxChainBone = {
						name: pmxBone.name + "+",
						englishName: pmxBone.englishName,
						position: [...pmxBone.position],
						parentBoneIndex: boneIndex,
						transformOrder: pmxBone.transformOrder,
						flag: pmxBone.flag & ~PmxObject.Bone.Flag.IsVisible & ~PmxObject.Bone.Flag.UseBoneIndexAsTailPosition,
						tailPosition: [
							0,
							0,
							0
						],
						appendTransform: pmxBone.appendTransform !== void 0 ? { ...pmxBone.appendTransform } : void 0,
						axisLimit: pmxBone.axisLimit !== void 0 ? [...pmxBone.axisLimit] : void 0,
						localVector: pmxBone.localVector !== void 0 ? {
							x: [...pmxBone.localVector.x],
							z: [...pmxBone.localVector.z]
						} : void 0,
						externalParentTransform: pmxBone.externalParentTransform,
						ik: void 0
					};
					ikChainBones.push(pmxChainBone);
					ikCount += 1;
				}
				if (pmxChainBone.ik === void 0) pmxChainBone.ik = {
					target: 0,
					iteration: 0,
					rotationConstraint: 0,
					links: []
				};
				{
					const pmxChainBoneIk = pmxChainBone.ik;
					pmxChainBoneIk.target = ik.targetIndex;
					pmxChainBoneIk.iteration = ik.iteration;
					pmxChainBoneIk.rotationConstraint = ik.rotationConstraint * 4;
					const ikLinks = ik.links;
					for (let ikLinkIndex = 0; ikLinkIndex < ikLinks.length; ++ikLinkIndex) {
						const ikLink = ikLinks[ikLinkIndex];
						if (0 <= ikLink && ikLink < finalBones.length) {
							const pmxIkLink = {
								target: ikLink,
								limitation: void 0
							};
							if (0 <= ikLink && ikLink < bones.length) {
								const chainName = bones[ikLink].name;
								const limitation = this._IkAngleLimitTable.get(chainName);
								if (limitation !== void 0) pmxIkLink.limitation = {
									minimumAngle: [
										limitation[0],
										limitation[2],
										limitation[4]
									],
									maximumAngle: [
										limitation[1],
										limitation[3],
										limitation[5]
									]
								};
							}
							pmxChainBoneIk.links.push(pmxIkLink);
						}
					}
				}
			}
		}
		finalBones.push(...ikChainBones);
		boneCount = Math.min(finalBones.length, bones.length);
		const ikIndexMap = [];
		for (let i = 0; i < boneCount; ++i) {
			if ((finalBones[i].flag & PmxObject.Bone.Flag.IsIkEnabled) === 0) continue;
			for (let j = 0; j < iks.length; ++j) if (iks[j].boneIndex === i) {
				ikIndexMap.push([i, j]);
				break;
			}
		}
		let isPmdAssendingOrder = true;
		for (let i = 0; i < ikIndexMap.length - 1; ++i) if (ikIndexMap[i][1] > ikIndexMap[i + 1][1]) {
			isPmdAssendingOrder = false;
			break;
		}
		if (!isPmdAssendingOrder) {
			ikIndexMap.sort((a, b) => a[1] - b[1]);
			const invalidOrderPmxBoneMap = new Array(ikIndexMap.length);
			for (let i = 1; i < ikIndexMap.length; ++i) {
				let isValid = true;
				if (ikIndexMap[i - 1][0] > ikIndexMap[i][0]) isValid = false;
				else if (invalidOrderPmxBoneMap[i - 1] !== void 0) isValid = false;
				if (!isValid) invalidOrderPmxBoneMap[i] = finalBones[ikIndexMap[i - 1][0]];
			}
			const pmxBoneToInvalidMap = /* @__PURE__ */ new Map();
			for (let i = 0; i < invalidOrderPmxBoneMap.length; ++i) {
				const pmxBone = invalidOrderPmxBoneMap[i];
				if (pmxBone !== void 0 && !pmxBoneToInvalidMap.has(pmxBone)) pmxBoneToInvalidMap.set(pmxBone, i);
			}
			const pmdSortedPmxBones = new Array(ikIndexMap.length);
			for (let i = 0; i < ikIndexMap.length; ++i) pmdSortedPmxBones[i] = finalBones[ikIndexMap[i][0]];
			const oldFinalBones = finalBones.slice();
			for (let i = 0; 0 < pmxBoneToInvalidMap.size; ++i) {
				for (let j = 1; j < ikIndexMap.length; ++j) if (invalidOrderPmxBoneMap[j] !== void 0 && pmdSortedPmxBones[j] !== void 0 && !pmxBoneToInvalidMap.has(pmdSortedPmxBones[j])) {
					const pmxBone = pmdSortedPmxBones[j];
					const removeIndex = finalBones.indexOf(pmxBone);
					finalBones.splice(removeIndex, 1);
					const insertIndex = finalBones.indexOf(invalidOrderPmxBoneMap[j]) + 1;
					finalBones.splice(insertIndex, 0, pmxBone);
					pmxBoneToInvalidMap.delete(pmxBone);
				}
				if (ikIndexMap.length < i) break;
			}
			const boneToIndexTable = /* @__PURE__ */ new Map();
			for (let i = 0; i < finalBones.length; ++i) {
				const pmxBone = finalBones[i];
				boneToIndexTable.set(pmxBone, i);
			}
			for (let i = 0; i < vertices.length; ++i) {
				const boneWeight = vertices[i].boneWeight;
				if (typeof boneWeight.boneIndices === "number") boneWeight.boneIndices = boneToIndexTable.get(oldFinalBones[boneWeight.boneIndices]);
				else {
					const boneIndices = boneWeight.boneIndices;
					for (let j = 0; j < boneIndices.length; ++j) boneIndices[j] = boneToIndexTable.get(oldFinalBones[boneIndices[j]]);
				}
			}
			for (let i = 0; i < finalBones.length; ++i) {
				const pmxBone = finalBones[i];
				pmxBone.parentBoneIndex = boneToIndexTable.get(oldFinalBones[pmxBone.parentBoneIndex]);
				if (typeof pmxBone.tailPosition === "number") pmxBone.tailPosition = boneToIndexTable.get(oldFinalBones[pmxBone.tailPosition]);
				if (pmxBone.appendTransform) pmxBone.appendTransform.parentIndex = boneToIndexTable.get(oldFinalBones[pmxBone.appendTransform.parentIndex]);
				if (pmxBone.ik) {
					pmxBone.ik.target = boneToIndexTable.get(oldFinalBones[pmxBone.ik.target]);
					const ikLinks = pmxBone.ik.links;
					for (let j = 0; j < ikLinks.length; ++j) ikLinks[j].target = boneToIndexTable.get(oldFinalBones[ikLinks[j].target]);
				}
			}
			for (let i = 0; i < displayFrames.length; ++i) {
				const frames = displayFrames[i].frames;
				if (frames === void 0) continue;
				for (let j = 0; j < frames.length; ++j) {
					const frame = frames[j];
					if (frame.type === PmxObject.DisplayFrame.FrameData.FrameType.Bone) frame.index = boneToIndexTable.get(oldFinalBones[frame.index]);
				}
			}
			if (rigidBodies !== void 0) for (let i = 0; i < rigidBodies.length; ++i) {
				const rigidBody = rigidBodies[i];
				rigidBody.boneIndex = boneToIndexTable.get(oldFinalBones[rigidBody.boneIndex]);
			}
		}
		let hasLoop = false;
		for (let i = 0; i < finalBones.length; ++i) {
			let pmxBone = finalBones[i];
			for (let j = 0; j < finalBones.length; ++j) {
				const parentBoneIndex = finalBones[j].parentBoneIndex;
				if (parentBoneIndex === i) {
					hasLoop = true;
					break;
				}
				pmxBone = finalBones[parentBoneIndex];
				if (pmxBone === void 0) break;
			}
			if (hasLoop) break;
		}
		if (hasLoop) for (let i = 0; i < finalBones.length; ++i) {
			let orderUpdated = false;
			for (let j = 0; j < finalBones.length; ++j) {
				const pmxBone = finalBones[j];
				let ancestorPmxBone = pmxBone;
				let transformOrder = pmxBone.transformOrder;
				for (;;) {
					const parentPmxBone = finalBones[ancestorPmxBone.parentBoneIndex];
					if (parentPmxBone === void 0) break;
					if (transformOrder < parentPmxBone.transformOrder) {
						transformOrder = parentPmxBone.transformOrder;
						orderUpdated = true;
					}
					ancestorPmxBone = parentPmxBone;
				}
				pmxBone.transformOrder = transformOrder;
			}
			if (!orderUpdated) break;
		}
		for (let i = 0; i < finalBones.length; ++i) {
			const pmxBone = finalBones[i];
			if ((pmxBone.flag & PmxObject.Bone.Flag.UseBoneIndexAsTailPosition) !== 0) {
				if (!(typeof pmxBone.tailPosition === "number")) pmxBone.tailPosition = -1;
			} else if (typeof pmxBone.tailPosition === "number") pmxBone.tailPosition = [
				0,
				0,
				0
			];
		}
		return finalBones;
	}
	static _ParseRigidBodies(dataDeserializer) {
		const rigidBodiesCount = dataDeserializer.getUint32();
		const rigidBodies = [];
		for (let i = 0; i < rigidBodiesCount; ++i) {
			const rigidBody = {
				name: dataDeserializer.getDecoderString(20, true),
				englishName: "",
				boneIndex: dataDeserializer.getInt16(),
				collisionGroup: dataDeserializer.getUint8(),
				collisionMask: dataDeserializer.getUint16(),
				shapeType: dataDeserializer.getUint8(),
				shapeSize: dataDeserializer.getFloat32Tuple(3),
				shapePosition: dataDeserializer.getFloat32Tuple(3),
				shapeRotation: dataDeserializer.getFloat32Tuple(3),
				mass: dataDeserializer.getFloat32(),
				linearDamping: dataDeserializer.getFloat32(),
				angularDamping: dataDeserializer.getFloat32(),
				repulsion: dataDeserializer.getFloat32(),
				friction: dataDeserializer.getFloat32(),
				physicsMode: dataDeserializer.getUint8()
			};
			rigidBodies.push(rigidBody);
		}
		return rigidBodies;
	}
	static _NormalizeRigidBodyPositions(rigidBodies, bones) {
		for (let i = 0; i < rigidBodies.length; ++i) {
			const rigidBody = rigidBodies[i];
			const bonePosition = bones[rigidBody.boneIndex < 0 ? 0 : rigidBody.boneIndex].position;
			const rigidBodyPosition = rigidBody.shapePosition;
			rigidBodyPosition[0] += bonePosition[0];
			rigidBodyPosition[1] += bonePosition[1];
			rigidBodyPosition[2] += bonePosition[2];
		}
	}
	static _ParseJoints(dataDeserializer) {
		const jointsCount = dataDeserializer.getUint32();
		const joints = [];
		for (let i = 0; i < jointsCount; ++i) {
			const name = dataDeserializer.getDecoderString(20, true);
			const rigidbodyIndexA = dataDeserializer.getInt32();
			const rigidbodyIndexB = dataDeserializer.getInt32();
			const position = dataDeserializer.getFloat32Tuple(3);
			const rotation = dataDeserializer.getFloat32Tuple(3);
			const positionMin = dataDeserializer.getFloat32Tuple(3);
			const positionMax = dataDeserializer.getFloat32Tuple(3);
			const rotationMin = dataDeserializer.getFloat32Tuple(3);
			const rotationMax = dataDeserializer.getFloat32Tuple(3);
			const springPosition = dataDeserializer.getFloat32Tuple(3);
			const springRotation = dataDeserializer.getFloat32Tuple(3);
			const joint = {
				name,
				englishName: "",
				type: PmxObject.Joint.Type.Spring6dof,
				rigidbodyIndexA,
				rigidbodyIndexB,
				position,
				rotation,
				positionMin,
				positionMax,
				rotationMin,
				rotationMax,
				springPosition,
				springRotation
			};
			joints.push(joint);
		}
		return joints;
	}
};

//#endregion
//#region ../../node_modules/.pnpm/babylon-mmd@1.0.0_@babylonjs+core@8.29.0/node_modules/babylon-mmd/esm/Loader/Parser/pmxReader.js
var IndexReader = class {
	_vertexIndexSize;
	_textureIndexSize;
	_materialIndexSize;
	_boneIndexSize;
	_morphIndexSize;
	_rigidBodyIndexSize;
	constructor(vertexIndexSize, textureIndexSize, materialIndexSize, boneIndexSize, morphIndexSize, rigidBodyIndexSize) {
		this._vertexIndexSize = vertexIndexSize;
		this._textureIndexSize = textureIndexSize;
		this._materialIndexSize = materialIndexSize;
		this._boneIndexSize = boneIndexSize;
		this._morphIndexSize = morphIndexSize;
		this._rigidBodyIndexSize = rigidBodyIndexSize;
	}
	getVertexIndex(dataDeserializer) {
		switch (this._vertexIndexSize) {
			case 1: return dataDeserializer.getUint8();
			case 2: return dataDeserializer.getUint16();
			case 4: return dataDeserializer.getInt32();
			default: throw new Error(`Invalid vertexIndexSize: ${this._vertexIndexSize}`);
		}
	}
	_getNonVertexIndex(dataDeserializer, indexSize) {
		switch (indexSize) {
			case 1: return dataDeserializer.getInt8();
			case 2: return dataDeserializer.getInt16();
			case 4: return dataDeserializer.getInt32();
			default: throw new Error(`Invalid indexSize: ${indexSize}`);
		}
	}
	getTextureIndex(dataDeserializer) {
		return this._getNonVertexIndex(dataDeserializer, this._textureIndexSize);
	}
	getMaterialIndex(dataDeserializer) {
		return this._getNonVertexIndex(dataDeserializer, this._materialIndexSize);
	}
	getBoneIndex(dataDeserializer) {
		return this._getNonVertexIndex(dataDeserializer, this._boneIndexSize);
	}
	getMorphIndex(dataDeserializer) {
		return this._getNonVertexIndex(dataDeserializer, this._morphIndexSize);
	}
	getRigidBodyIndex(dataDeserializer) {
		return this._getNonVertexIndex(dataDeserializer, this._rigidBodyIndexSize);
	}
};
/**
* PmxReader is a static class that parses PMX data
*/
var PmxReader = class {
	constructor() {}
	/**
	* Parses PMX data asynchronously
	* @param data Arraybuffer of PMX data
	* @param logger Logger
	* @returns PMX data
	* @throws {Error} If the parse fails
	*/
	static async ParseAsync(data, logger = new ConsoleLogger()) {
		const dataDeserializer = new MmdDataDeserializer(data);
		const header = this._ParseHeader(dataDeserializer, logger);
		const indexReader = new IndexReader(header.vertexIndexSize, header.textureIndexSize, header.materialIndexSize, header.boneIndexSize, header.morphIndexSize, header.rigidBodyIndexSize);
		const vertices = await this._ParseVerticesAsync(dataDeserializer, indexReader, header);
		const indices = this._ParseIndices(dataDeserializer, indexReader, header);
		const textures = this._ParseTextures(dataDeserializer);
		const materials = this._ParseMaterials(dataDeserializer, indexReader);
		const bones = this._ParseBones(dataDeserializer, indexReader);
		const morphs = this._ParseMorphs(dataDeserializer, indexReader);
		const displayFrames = this._ParseDisplayFrames(dataDeserializer, indexReader);
		const rigidBodies = this._ParseRigidBodies(dataDeserializer, indexReader);
		const joints = this._ParseJoints(dataDeserializer, indexReader);
		const softBodies = header.version <= 2 ? [] : this._ParseSoftBodies(dataDeserializer, indexReader, header);
		if (dataDeserializer.bytesAvailable > 0) logger.warn(`There are ${dataDeserializer.bytesAvailable} bytes left after parsing`);
		return {
			header,
			vertices,
			indices,
			textures,
			materials,
			bones,
			morphs,
			displayFrames,
			rigidBodies,
			joints,
			softBodies
		};
	}
	static _ParseHeader(dataDeserializer, logger) {
		if (dataDeserializer.bytesAvailable < 17) throw new RangeError("is not pmx file");
		const signature = dataDeserializer.getSignatureString(3);
		if (signature !== "PMX") throw new RangeError("is not pmx file");
		dataDeserializer.getInt8();
		const version = dataDeserializer.getFloat32();
		const globalsCount = dataDeserializer.getUint8();
		const encoding = dataDeserializer.getUint8();
		dataDeserializer.initializeTextDecoder(encoding === PmxObject.Header.Encoding.Utf8 ? "utf-8" : "utf-16le");
		const additionalVec4Count = dataDeserializer.getUint8();
		const vertexIndexSize = dataDeserializer.getUint8();
		const textureIndexSize = dataDeserializer.getUint8();
		const materialIndexSize = dataDeserializer.getUint8();
		const boneIndexSize = dataDeserializer.getUint8();
		const morphIndexSize = dataDeserializer.getUint8();
		const rigidBodyIndexSize = dataDeserializer.getUint8();
		if (globalsCount < 8) throw new Error(`Invalid globalsCount: ${globalsCount}`);
		else if (8 < globalsCount) {
			logger.warn(`globalsCount is greater than 8: ${globalsCount} files may be corrupted or higher version`);
			for (let i = 8; i < globalsCount; ++i) dataDeserializer.getUint8();
		}
		return {
			signature,
			version,
			encoding,
			additionalVec4Count,
			vertexIndexSize,
			textureIndexSize,
			materialIndexSize,
			boneIndexSize,
			morphIndexSize,
			rigidBodyIndexSize,
			modelName: dataDeserializer.getDecoderString(dataDeserializer.getInt32(), false),
			englishModelName: dataDeserializer.getDecoderString(dataDeserializer.getInt32(), false),
			comment: dataDeserializer.getDecoderString(dataDeserializer.getInt32(), false),
			englishComment: dataDeserializer.getDecoderString(dataDeserializer.getInt32(), false)
		};
	}
	static async _ParseVerticesAsync(dataDeserializer, indexReader, header) {
		const verticesCount = dataDeserializer.getInt32();
		const vertices = [];
		let time = performance.now();
		for (let i = 0; i < verticesCount; ++i) {
			const position = dataDeserializer.getFloat32Tuple(3);
			const normal = dataDeserializer.getFloat32Tuple(3);
			const uv = dataDeserializer.getFloat32Tuple(2);
			const additionalVec4 = [];
			for (let j = 0; j < header.additionalVec4Count; ++j) additionalVec4.push(dataDeserializer.getFloat32Tuple(4));
			const weightType = dataDeserializer.getUint8();
			let boneWeight;
			switch (weightType) {
				case PmxObject.Vertex.BoneWeightType.Bdef1:
					boneWeight = {
						boneIndices: indexReader.getBoneIndex(dataDeserializer),
						boneWeights: null
					};
					break;
				case PmxObject.Vertex.BoneWeightType.Bdef2:
					boneWeight = {
						boneIndices: [indexReader.getBoneIndex(dataDeserializer), indexReader.getBoneIndex(dataDeserializer)],
						boneWeights: dataDeserializer.getFloat32()
					};
					break;
				case PmxObject.Vertex.BoneWeightType.Bdef4:
					boneWeight = {
						boneIndices: [
							indexReader.getBoneIndex(dataDeserializer),
							indexReader.getBoneIndex(dataDeserializer),
							indexReader.getBoneIndex(dataDeserializer),
							indexReader.getBoneIndex(dataDeserializer)
						],
						boneWeights: [
							dataDeserializer.getFloat32(),
							dataDeserializer.getFloat32(),
							dataDeserializer.getFloat32(),
							dataDeserializer.getFloat32()
						]
					};
					break;
				case PmxObject.Vertex.BoneWeightType.Sdef:
					boneWeight = {
						boneIndices: [indexReader.getBoneIndex(dataDeserializer), indexReader.getBoneIndex(dataDeserializer)],
						boneWeights: {
							boneWeight0: dataDeserializer.getFloat32(),
							c: dataDeserializer.getFloat32Tuple(3),
							r0: dataDeserializer.getFloat32Tuple(3),
							r1: dataDeserializer.getFloat32Tuple(3)
						}
					};
					break;
				case PmxObject.Vertex.BoneWeightType.Qdef:
					boneWeight = {
						boneIndices: [
							indexReader.getBoneIndex(dataDeserializer),
							indexReader.getBoneIndex(dataDeserializer),
							indexReader.getBoneIndex(dataDeserializer),
							indexReader.getBoneIndex(dataDeserializer)
						],
						boneWeights: [
							dataDeserializer.getFloat32(),
							dataDeserializer.getFloat32(),
							dataDeserializer.getFloat32(),
							dataDeserializer.getFloat32()
						]
					};
					break;
				default: throw new Error(`Invalid weightType: ${weightType}`);
			}
			const edgeScale = dataDeserializer.getFloat32();
			vertices.push({
				position,
				normal,
				uv,
				additionalVec4,
				weightType,
				boneWeight,
				edgeScale
			});
			if (i % 1e4 === 0 && 100 < performance.now() - time) {
				await new Promise((resolve) => setTimeout(resolve, 0));
				time = performance.now();
			}
		}
		return vertices;
	}
	static _ParseIndices(dataDeserializer, indexReader, header) {
		const indicesCount = dataDeserializer.getInt32();
		const indexArrayBuffer = new ArrayBuffer(indicesCount * header.vertexIndexSize);
		let indices;
		switch (header.vertexIndexSize) {
			case 1:
				indices = new Uint8Array(indexArrayBuffer);
				break;
			case 2:
				indices = new Uint16Array(indexArrayBuffer);
				break;
			case 4:
				indices = new Int32Array(indexArrayBuffer);
				break;
			default: throw new Error(`Invalid vertexIndexSize: ${header.vertexIndexSize}`);
		}
		for (let i = 0; i < indicesCount; ++i) indices[i] = indexReader.getVertexIndex(dataDeserializer);
		return indices;
	}
	static _ParseTextures(dataDeserializer) {
		const texturesCount = dataDeserializer.getInt32();
		const textures = [];
		for (let i = 0; i < texturesCount; ++i) {
			const textureName = dataDeserializer.getDecoderString(dataDeserializer.getInt32(), false);
			textures.push(textureName);
		}
		return textures;
	}
	static _ParseMaterials(dataDeserializer, indexReader) {
		const materialsCount = dataDeserializer.getInt32();
		const materials = [];
		for (let i = 0; i < materialsCount; ++i) {
			const name = dataDeserializer.getDecoderString(dataDeserializer.getInt32(), false);
			const englishName = dataDeserializer.getDecoderString(dataDeserializer.getInt32(), false);
			const diffuse = dataDeserializer.getFloat32Tuple(4);
			const specular = dataDeserializer.getFloat32Tuple(3);
			const shininess = dataDeserializer.getFloat32();
			const ambient = dataDeserializer.getFloat32Tuple(3);
			const flag = dataDeserializer.getUint8();
			const edgeColor = dataDeserializer.getFloat32Tuple(4);
			const edgeSize = dataDeserializer.getFloat32();
			const textureIndex = indexReader.getTextureIndex(dataDeserializer);
			const sphereTextureIndex = indexReader.getTextureIndex(dataDeserializer);
			const sphereTextureMode = dataDeserializer.getUint8();
			const isSharedToonTexture = dataDeserializer.getUint8() === 1;
			const material = {
				name,
				englishName,
				diffuse,
				specular,
				shininess,
				ambient,
				flag,
				edgeColor,
				edgeSize,
				textureIndex,
				sphereTextureIndex,
				sphereTextureMode,
				isSharedToonTexture,
				toonTextureIndex: isSharedToonTexture ? dataDeserializer.getUint8() : indexReader.getTextureIndex(dataDeserializer),
				comment: dataDeserializer.getDecoderString(dataDeserializer.getInt32(), false),
				indexCount: dataDeserializer.getInt32()
			};
			materials.push(material);
		}
		return materials;
	}
	static _ParseBones(dataDeserializer, indexReader) {
		const bonesCount = dataDeserializer.getInt32();
		const bones = [];
		for (let i = 0; i < bonesCount; ++i) {
			const name = dataDeserializer.getDecoderString(dataDeserializer.getInt32(), false);
			const englishName = dataDeserializer.getDecoderString(dataDeserializer.getInt32(), false);
			const position = dataDeserializer.getFloat32Tuple(3);
			const parentBoneIndex = indexReader.getBoneIndex(dataDeserializer);
			const transformOrder = dataDeserializer.getInt32();
			const flag = dataDeserializer.getUint16();
			let tailPosition;
			if (flag & PmxObject.Bone.Flag.UseBoneIndexAsTailPosition) tailPosition = indexReader.getBoneIndex(dataDeserializer);
			else tailPosition = dataDeserializer.getFloat32Tuple(3);
			let appendTransform;
			if (flag & PmxObject.Bone.Flag.HasAppendMove || flag & PmxObject.Bone.Flag.HasAppendRotate) appendTransform = {
				parentIndex: indexReader.getBoneIndex(dataDeserializer),
				ratio: dataDeserializer.getFloat32()
			};
			let axisLimit;
			if (flag & PmxObject.Bone.Flag.HasAxisLimit) axisLimit = dataDeserializer.getFloat32Tuple(3);
			let localVector;
			if (flag & PmxObject.Bone.Flag.HasLocalVector) localVector = {
				x: dataDeserializer.getFloat32Tuple(3),
				z: dataDeserializer.getFloat32Tuple(3)
			};
			let externalParentTransform;
			if (flag & PmxObject.Bone.Flag.IsExternalParentTransformed) externalParentTransform = dataDeserializer.getInt32();
			let ik;
			if (flag & PmxObject.Bone.Flag.IsIkEnabled) {
				const target = indexReader.getBoneIndex(dataDeserializer);
				const iteration = dataDeserializer.getInt32();
				const rotationConstraint = dataDeserializer.getFloat32();
				const links = [];
				const linksCount = dataDeserializer.getInt32();
				for (let i$1 = 0; i$1 < linksCount; ++i$1) {
					const link = {
						target: indexReader.getBoneIndex(dataDeserializer),
						limitation: dataDeserializer.getUint8() === 1 ? {
							minimumAngle: dataDeserializer.getFloat32Tuple(3),
							maximumAngle: dataDeserializer.getFloat32Tuple(3)
						} : void 0
					};
					links.push(link);
				}
				ik = {
					target,
					iteration,
					rotationConstraint,
					links
				};
			}
			const bone = {
				name,
				englishName,
				position,
				parentBoneIndex,
				transformOrder,
				flag,
				tailPosition,
				appendTransform,
				axisLimit,
				localVector,
				externalParentTransform,
				ik
			};
			bones.push(bone);
		}
		return bones;
	}
	static _ParseMorphs(dataDeserializer, indexReader) {
		const morphsCount = dataDeserializer.getInt32();
		const morphs = [];
		for (let i = 0; i < morphsCount; ++i) {
			const name = dataDeserializer.getDecoderString(dataDeserializer.getInt32(), false);
			const englishName = dataDeserializer.getDecoderString(dataDeserializer.getInt32(), false);
			const category = dataDeserializer.getInt8();
			const type = dataDeserializer.getInt8();
			let morph = {
				name,
				englishName,
				category,
				type
			};
			const morphOffsetCount = dataDeserializer.getInt32();
			switch (type) {
				case PmxObject.Morph.Type.GroupMorph:
					{
						const indices = new Int32Array(morphOffsetCount);
						const ratios = new Float32Array(morphOffsetCount);
						for (let i$1 = 0; i$1 < morphOffsetCount; ++i$1) {
							indices[i$1] = indexReader.getMorphIndex(dataDeserializer);
							ratios[i$1] = dataDeserializer.getFloat32();
						}
						morph = {
							...morph,
							indices,
							ratios
						};
					}
					break;
				case PmxObject.Morph.Type.VertexMorph:
					{
						const indices = new Int32Array(morphOffsetCount);
						const positions = new Float32Array(morphOffsetCount * 3);
						for (let i$1 = 0; i$1 < morphOffsetCount; ++i$1) {
							indices[i$1] = indexReader.getVertexIndex(dataDeserializer);
							positions[i$1 * 3 + 0] = dataDeserializer.getFloat32();
							positions[i$1 * 3 + 1] = dataDeserializer.getFloat32();
							positions[i$1 * 3 + 2] = dataDeserializer.getFloat32();
						}
						morph = {
							...morph,
							indices,
							positions
						};
					}
					break;
				case PmxObject.Morph.Type.BoneMorph:
					{
						const indices = new Int32Array(morphOffsetCount);
						const positions = new Float32Array(morphOffsetCount * 3);
						const rotations = new Float32Array(morphOffsetCount * 4);
						for (let i$1 = 0; i$1 < morphOffsetCount; ++i$1) {
							indices[i$1] = indexReader.getBoneIndex(dataDeserializer);
							positions[i$1 * 3 + 0] = dataDeserializer.getFloat32();
							positions[i$1 * 3 + 1] = dataDeserializer.getFloat32();
							positions[i$1 * 3 + 2] = dataDeserializer.getFloat32();
							rotations[i$1 * 4 + 0] = dataDeserializer.getFloat32();
							rotations[i$1 * 4 + 1] = dataDeserializer.getFloat32();
							rotations[i$1 * 4 + 2] = dataDeserializer.getFloat32();
							rotations[i$1 * 4 + 3] = dataDeserializer.getFloat32();
						}
						morph = {
							...morph,
							indices,
							positions,
							rotations
						};
					}
					break;
				case PmxObject.Morph.Type.UvMorph:
				case PmxObject.Morph.Type.AdditionalUvMorph1:
				case PmxObject.Morph.Type.AdditionalUvMorph2:
				case PmxObject.Morph.Type.AdditionalUvMorph3:
				case PmxObject.Morph.Type.AdditionalUvMorph4:
					{
						const indices = new Int32Array(morphOffsetCount);
						const offsets = new Float32Array(morphOffsetCount * 4);
						for (let i$1 = 0; i$1 < morphOffsetCount; ++i$1) {
							indices[i$1] = indexReader.getVertexIndex(dataDeserializer);
							offsets[i$1 * 4 + 0] = dataDeserializer.getFloat32();
							offsets[i$1 * 4 + 1] = dataDeserializer.getFloat32();
							offsets[i$1 * 4 + 2] = dataDeserializer.getFloat32();
							offsets[i$1 * 4 + 3] = dataDeserializer.getFloat32();
						}
						morph = {
							...morph,
							indices,
							offsets
						};
					}
					break;
				case PmxObject.Morph.Type.MaterialMorph:
					{
						const elements = [];
						for (let i$1 = 0; i$1 < morphOffsetCount; ++i$1) {
							const element = {
								index: indexReader.getMaterialIndex(dataDeserializer),
								type: dataDeserializer.getUint8(),
								diffuse: dataDeserializer.getFloat32Tuple(4),
								specular: dataDeserializer.getFloat32Tuple(3),
								shininess: dataDeserializer.getFloat32(),
								ambient: dataDeserializer.getFloat32Tuple(3),
								edgeColor: dataDeserializer.getFloat32Tuple(4),
								edgeSize: dataDeserializer.getFloat32(),
								textureColor: dataDeserializer.getFloat32Tuple(4),
								sphereTextureColor: dataDeserializer.getFloat32Tuple(4),
								toonTextureColor: dataDeserializer.getFloat32Tuple(4)
							};
							elements.push(element);
						}
						morph = {
							...morph,
							elements
						};
					}
					break;
				case PmxObject.Morph.Type.FlipMorph:
					{
						const indices = new Int32Array(morphOffsetCount);
						const ratios = new Float32Array(morphOffsetCount);
						for (let i$1 = 0; i$1 < morphOffsetCount; ++i$1) {
							indices[i$1] = indexReader.getMorphIndex(dataDeserializer);
							ratios[i$1] = dataDeserializer.getFloat32();
						}
						morph = {
							...morph,
							indices,
							ratios
						};
					}
					break;
				case PmxObject.Morph.Type.ImpulseMorph:
					{
						const indices = new Int32Array(morphOffsetCount);
						const isLocals = new Array(morphOffsetCount);
						const velocities = new Float32Array(morphOffsetCount * 3);
						const torques = new Float32Array(morphOffsetCount * 3);
						for (let i$1 = 0; i$1 < morphOffsetCount; ++i$1) {
							indices[i$1] = indexReader.getRigidBodyIndex(dataDeserializer);
							isLocals[i$1] = dataDeserializer.getUint8() === 1;
							velocities[i$1 * 3 + 0] = dataDeserializer.getFloat32();
							velocities[i$1 * 3 + 1] = dataDeserializer.getFloat32();
							velocities[i$1 * 3 + 2] = dataDeserializer.getFloat32();
							torques[i$1 * 3 + 0] = dataDeserializer.getFloat32();
							torques[i$1 * 3 + 1] = dataDeserializer.getFloat32();
							torques[i$1 * 3 + 2] = dataDeserializer.getFloat32();
						}
						morph = {
							...morph,
							indices,
							isLocals,
							velocities,
							torques
						};
					}
					break;
				default: throw new Error(`Unknown morph type: ${type}`);
			}
			morphs.push(morph);
		}
		return morphs;
	}
	static _ParseDisplayFrames(dataDeserializer, indexReader) {
		const displayFramesCount = dataDeserializer.getInt32();
		const displayFrames = [];
		for (let i = 0; i < displayFramesCount; ++i) {
			const name = dataDeserializer.getDecoderString(dataDeserializer.getInt32(), false);
			const englishName = dataDeserializer.getDecoderString(dataDeserializer.getInt32(), false);
			const isSpecialFrame = dataDeserializer.getUint8() === 1;
			const elementsCount = dataDeserializer.getInt32();
			const frames = [];
			for (let i$1 = 0; i$1 < elementsCount; ++i$1) {
				const frameType = dataDeserializer.getUint8();
				const frame = {
					type: frameType,
					index: frameType === PmxObject.DisplayFrame.FrameData.FrameType.Bone ? indexReader.getBoneIndex(dataDeserializer) : indexReader.getMorphIndex(dataDeserializer)
				};
				frames.push(frame);
			}
			const displayFrame = {
				name,
				englishName,
				isSpecialFrame,
				frames
			};
			displayFrames.push(displayFrame);
		}
		return displayFrames;
	}
	static _ParseRigidBodies(dataDeserializer, indexReader) {
		const rigidBodiesCount = dataDeserializer.getInt32();
		const rigidBodies = [];
		for (let i = 0; i < rigidBodiesCount; ++i) {
			const rigidBody = {
				name: dataDeserializer.getDecoderString(dataDeserializer.getInt32(), false),
				englishName: dataDeserializer.getDecoderString(dataDeserializer.getInt32(), false),
				boneIndex: indexReader.getBoneIndex(dataDeserializer),
				collisionGroup: dataDeserializer.getUint8(),
				collisionMask: dataDeserializer.getUint16(),
				shapeType: dataDeserializer.getUint8(),
				shapeSize: dataDeserializer.getFloat32Tuple(3),
				shapePosition: dataDeserializer.getFloat32Tuple(3),
				shapeRotation: dataDeserializer.getFloat32Tuple(3),
				mass: dataDeserializer.getFloat32(),
				linearDamping: dataDeserializer.getFloat32(),
				angularDamping: dataDeserializer.getFloat32(),
				repulsion: dataDeserializer.getFloat32(),
				friction: dataDeserializer.getFloat32(),
				physicsMode: dataDeserializer.getUint8()
			};
			rigidBodies.push(rigidBody);
		}
		return rigidBodies;
	}
	static _ParseJoints(dataDeserializer, indexReader) {
		const jointsCount = dataDeserializer.getInt32();
		const joints = [];
		for (let i = 0; i < jointsCount; ++i) {
			const joint = {
				name: dataDeserializer.getDecoderString(dataDeserializer.getInt32(), false),
				englishName: dataDeserializer.getDecoderString(dataDeserializer.getInt32(), false),
				type: dataDeserializer.getUint8(),
				rigidbodyIndexA: indexReader.getRigidBodyIndex(dataDeserializer),
				rigidbodyIndexB: indexReader.getRigidBodyIndex(dataDeserializer),
				position: dataDeserializer.getFloat32Tuple(3),
				rotation: dataDeserializer.getFloat32Tuple(3),
				positionMin: dataDeserializer.getFloat32Tuple(3),
				positionMax: dataDeserializer.getFloat32Tuple(3),
				rotationMin: dataDeserializer.getFloat32Tuple(3),
				rotationMax: dataDeserializer.getFloat32Tuple(3),
				springPosition: dataDeserializer.getFloat32Tuple(3),
				springRotation: dataDeserializer.getFloat32Tuple(3)
			};
			joints.push(joint);
		}
		return joints;
	}
	static _ParseSoftBodies(dataDeserializer, indexReader, header) {
		const softBodiesCount = dataDeserializer.getInt32();
		const softBodies = [];
		for (let i = 0; i < softBodiesCount; ++i) {
			const name = dataDeserializer.getDecoderString(dataDeserializer.getInt32(), false);
			const englishName = dataDeserializer.getDecoderString(dataDeserializer.getInt32(), false);
			const type = dataDeserializer.getUint8();
			const materialIndex = indexReader.getMaterialIndex(dataDeserializer);
			const collisionGroup = dataDeserializer.getUint8();
			const collisionMask = dataDeserializer.getUint16();
			const flags = dataDeserializer.getUint8();
			const bLinkDistance = dataDeserializer.getInt32();
			const clusterCount = dataDeserializer.getInt32();
			const totalMass = dataDeserializer.getFloat32();
			const collisionMargin = dataDeserializer.getFloat32();
			const aeroModel = dataDeserializer.getInt32();
			const config = {
				vcf: dataDeserializer.getFloat32(),
				dp: dataDeserializer.getFloat32(),
				dg: dataDeserializer.getFloat32(),
				lf: dataDeserializer.getFloat32(),
				pr: dataDeserializer.getFloat32(),
				vc: dataDeserializer.getFloat32(),
				df: dataDeserializer.getFloat32(),
				mt: dataDeserializer.getFloat32(),
				chr: dataDeserializer.getFloat32(),
				khr: dataDeserializer.getFloat32(),
				shr: dataDeserializer.getFloat32(),
				ahr: dataDeserializer.getFloat32()
			};
			const cluster = {
				srhrCl: dataDeserializer.getFloat32(),
				skhrCl: dataDeserializer.getFloat32(),
				sshrCl: dataDeserializer.getFloat32(),
				srSpltCl: dataDeserializer.getFloat32(),
				skSpltCl: dataDeserializer.getFloat32(),
				ssSpltCl: dataDeserializer.getFloat32()
			};
			const iteration = {
				vIt: dataDeserializer.getInt32(),
				pIt: dataDeserializer.getInt32(),
				dIt: dataDeserializer.getInt32(),
				cIt: dataDeserializer.getInt32()
			};
			const material = {
				lst: dataDeserializer.getInt32(),
				ast: dataDeserializer.getInt32(),
				vst: dataDeserializer.getInt32()
			};
			const anchorsCount = dataDeserializer.getInt32();
			const anchors = [];
			for (let j = 0; j < anchorsCount; ++j) {
				const anchorRigidBody = {
					rigidbodyIndex: indexReader.getRigidBodyIndex(dataDeserializer),
					vertexIndex: indexReader.getVertexIndex(dataDeserializer),
					isNearMode: dataDeserializer.getUint8() !== 0
				};
				anchors.push(anchorRigidBody);
			}
			const vertexPinCount = dataDeserializer.getInt32();
			const vertexPinArrayBuffer = new ArrayBuffer(vertexPinCount * header.vertexIndexSize);
			let vertexPins;
			switch (header.vertexIndexSize) {
				case 1:
					vertexPins = new Uint8Array(vertexPinArrayBuffer);
					break;
				case 2:
					vertexPins = new Uint16Array(vertexPinArrayBuffer);
					break;
				case 4:
					vertexPins = new Int32Array(vertexPinArrayBuffer);
					break;
				default: throw new Error(`Invalid vertexIndexSize: ${header.vertexIndexSize}`);
			}
			for (let i$1 = 0; i$1 < vertexPinCount; ++i$1) vertexPins[i$1] = indexReader.getVertexIndex(dataDeserializer);
			const softBody = {
				name,
				englishName,
				type,
				materialIndex,
				collisionGroup,
				collisionMask,
				flags,
				bLinkDistance,
				clusterCount,
				totalMass,
				collisionMargin,
				aeroModel,
				config,
				cluster,
				iteration,
				material,
				anchors,
				vertexPins
			};
			softBodies.push(softBody);
		}
		return softBodies;
	}
};

//#endregion
//#region src/utils/_extract-model-extension.ts
const extractModelExtension = (buffer) => {
	const decoder = new TextDecoder("utf-8");
	const bytes = new Uint8Array(buffer, 0, 3);
	return decoder.decode(bytes).toLowerCase();
};

//#endregion
//#region src/utils/mmd.ts
var MMD = class {
	constructor(pmx, mesh, grants, iks) {
		this.grants = [];
		this.iks = [];
		this.pmx = pmx;
		this.grants = grants;
		this.iks = iks;
		this.mesh = mesh;
		this.scale = 1;
	}
	createHelper() {
		return this.physics?.createHelper?.();
	}
	setPhysics(createPhysics) {
		this.physics = createPhysics(this);
	}
	setScalar(scale) {
		if (this.scale === scale) return;
		this.scale = scale;
		this.mesh.scale.setScalar(scale);
		this.physics?.setScalar?.(this.scale);
	}
	update(delta) {
		if (!this.physics) return;
		this.physics.update(delta);
	}
};

//#endregion
//#region src/utils/build-bones.ts
const buildBones = (pmx, mesh) => {
	const bones = pmx.bones.map((boneInfo) => {
		const bone = new Bone();
		bone.name = boneInfo.name;
		const pos = [...boneInfo.position];
		if (boneInfo.parentBoneIndex >= 0 && boneInfo.parentBoneIndex < pmx.bones.length) {
			const parentInfo = pmx.bones[boneInfo.parentBoneIndex];
			pos[0] -= parentInfo.position[0];
			pos[1] -= parentInfo.position[1];
			pos[2] -= parentInfo.position[2];
		}
		bone.position.fromArray(pos);
		return bone;
	});
	pmx.bones.forEach((boneInfo, i) => {
		if (boneInfo.parentBoneIndex >= 0 && boneInfo.parentBoneIndex < pmx.bones.length) bones[boneInfo.parentBoneIndex].add(bones[i]);
		else mesh.add(bones[i]);
	});
	mesh.updateMatrixWorld(true);
	const skeleton = new Skeleton(bones);
	mesh.bind(skeleton);
	return mesh;
};

//#endregion
//#region src/utils/build-geometry.ts
const buildGeometry = (pmx) => {
	const geometry = new BufferGeometry();
	const vertexCount = pmx.vertices.length;
	const positions = new Float32Array(vertexCount * 3);
	const normals = new Float32Array(vertexCount * 3);
	const uvs = new Float32Array(vertexCount * 2);
	const skinIndices = new Uint16Array(vertexCount * 4);
	const skinWeights = new Float32Array(vertexCount * 4);
	pmx.vertices.forEach((v, i) => {
		const position = [
			v.position[0],
			v.position[1],
			v.position[2]
		];
		const normal = [
			v.normal[0],
			v.normal[1],
			v.normal[2]
		];
		positions.set(position, i * 3);
		normals.set(normal, i * 3);
		uvs.set(v.uv, i * 2);
		switch (v.weightType) {
			case PmxObject.Vertex.BoneWeightType.Bdef1: {
				const bw = v.boneWeight;
				skinIndices.set([
					bw.boneIndices,
					0,
					0,
					0
				], i * 4);
				skinWeights.set([
					1,
					0,
					0,
					0
				], i * 4);
				break;
			}
			case PmxObject.Vertex.BoneWeightType.Bdef2: {
				const bw = v.boneWeight;
				skinIndices.set(bw.boneIndices, i * 4);
				skinWeights.set([
					bw.boneWeights,
					1 - bw.boneWeights,
					0,
					0
				], i * 4);
				break;
			}
			case PmxObject.Vertex.BoneWeightType.Bdef4:
			case PmxObject.Vertex.BoneWeightType.Qdef: {
				const bw = v.boneWeight;
				skinIndices.set(bw.boneIndices, i * 4);
				skinWeights.set(bw.boneWeights, i * 4);
				break;
			}
			case PmxObject.Vertex.BoneWeightType.Sdef: {
				const bw = v.boneWeight;
				skinIndices.set([
					bw.boneIndices[0],
					bw.boneIndices[1],
					0,
					0
				], i * 4);
				const sdefWeights = bw.boneWeights;
				skinWeights.set([
					sdefWeights.boneWeight0,
					1 - sdefWeights.boneWeight0,
					0,
					0
				], i * 4);
			}
		}
	});
	geometry.setAttribute("position", new BufferAttribute(positions, 3));
	geometry.setAttribute("normal", new BufferAttribute(normals, 3));
	geometry.setAttribute("uv", new BufferAttribute(uvs, 2));
	geometry.setAttribute("skinIndex", new BufferAttribute(skinIndices, 4));
	geometry.setAttribute("skinWeight", new BufferAttribute(skinWeights, 4));
	const indices = Array.from(pmx.indices);
	geometry.setIndex(indices);
	let faceIndex = 0;
	for (const material of pmx.materials) {
		geometry.addGroup(faceIndex, material.indexCount, pmx.materials.indexOf(material));
		faceIndex += material.indexCount;
	}
	const morphPositions = [];
	const updateAttributes = (attribute, morph, ratio) => {
		if (morph.type !== PmxObject.Morph.Type.VertexMorph) return;
		for (let i = 0; i < morph.indices.length; i++) {
			const index = morph.indices[i];
			attribute.array[index * 3 + 0] += morph.positions[i * 3 + 0] * ratio;
			attribute.array[index * 3 + 1] += morph.positions[i * 3 + 1] * ratio;
			attribute.array[index * 3 + 2] += morph.positions[i * 3 + 2] * ratio;
		}
	};
	for (const morph of pmx.morphs) {
		if (morph.type !== PmxObject.Morph.Type.VertexMorph && morph.type !== PmxObject.Morph.Type.GroupMorph) continue;
		const attribute = new BufferAttribute(positions.slice(), 3);
		attribute.name = morph.name;
		if (morph.type === PmxObject.Morph.Type.GroupMorph) for (let i = 0; i < morph.indices.length; i++) {
			const targetMorph = pmx.morphs[morph.indices[i]];
			const ratio = morph.ratios[i];
			if (targetMorph.type === PmxObject.Morph.Type.VertexMorph) updateAttributes(attribute, targetMorph, ratio);
		}
		else updateAttributes(attribute, morph, 1);
		morphPositions.push(attribute);
	}
	if (morphPositions.length > 0) {
		geometry.morphAttributes.position = morphPositions;
		geometry.morphTargetsRelative = false;
	}
	geometry.computeBoundingSphere();
	return geometry;
};

//#endregion
//#region src/utils/build-grants.ts
const buildGrants = (pmx) => {
	const grantMap = {};
	pmx.bones.forEach((bone, i) => {
		const at = bone.appendTransform;
		if (!at) return;
		const flags = bone.flag;
		const affectRotation = (flags & PmxObject.Bone.Flag.HasAppendRotate) !== 0;
		const affectPosition = (flags & PmxObject.Bone.Flag.HasAppendMove) !== 0;
		if (!affectRotation && !affectPosition) return;
		grantMap[i] = {
			children: [],
			param: {
				affectPosition,
				affectRotation,
				index: i,
				isLocal: (flags & PmxObject.Bone.Flag.LocalAppendTransform) !== 0,
				parentIndex: at.parentIndex,
				ratio: at.ratio,
				transformationClass: bone.transformOrder
			},
			visited: false
		};
	});
	const root = {
		children: [],
		visited: false
	};
	Object.values(grantMap).forEach((entry) => {
		if (!entry.param) return;
		(grantMap[entry.param.parentIndex] ?? root).children.push(entry);
	});
	const grants = [];
	const walk = (e) => {
		if (e.visited) return;
		e.visited = true;
		if (e.param) grants.push(e.param);
		e.children.forEach(walk);
	};
	walk(root);
	return grants;
};

//#endregion
//#region src/utils/build-ik.ts
const buildIK = (pmx) => {
	const iks = [];
	for (const [index, { ik }] of pmx.bones.entries()) {
		if (ik === void 0) continue;
		const param = {
			effector: ik.target,
			iteration: ik.iteration,
			links: [],
			maxAngle: ik.rotationConstraint > 0 ? ik.rotationConstraint : void 0,
			target: index
		};
		const links = ik.links.map((link) => {
			const result = {
				enabled: true,
				index: link.target
			};
			if (pmx.bones[link.target].name.includes("ひざ")) result.limitation = new Vector3(1, 0, 0);
			else if (link.limitation) {
				const rotationMin = link.limitation.minimumAngle;
				const rotationMax = link.limitation.maximumAngle;
				result.rotationMin = new Vector3().fromArray(rotationMin);
				result.rotationMax = new Vector3().fromArray(rotationMax);
			}
			return result;
		});
		iks.push({
			...param,
			links
		});
	}
	return iks;
};

//#endregion
//#region src/shaders/mmd-toon-shader.ts
/**
* MMD Toon Shader
*
* This shader is extended from MeshPhongMaterial, and merged algorithms with
* MeshToonMaterial and MeshMetcapMaterial.
* Ideas came from https://github.com/mrdoob/three.js/issues/19609
*
* Combining steps:
*  Declare matcap uniform.
*  Add gradientmap_pars_fragment.
*  Use gradient irradiances instead of dotNL irradiance from MeshPhongMaterial.
*    (Replace lights_phong_pars_fragment with lights_mmd_toon_pars_fragment)
*  Add mmd_toon_matcap_fragment.
*/
const lights_mmd_toon_pars_fragment = `
varying vec3 vViewPosition;

struct BlinnPhongMaterial {

	vec3 diffuseColor;
	vec3 specularColor;
	float specularShininess;
	float specularStrength;

};

void RE_Direct_BlinnPhong( const in IncidentLight directLight, const in vec3 geometryPosition, const in vec3 geometryNormal, const in vec3 geometryViewDir, const in vec3 geometryClearcoatNormal, const in BlinnPhongMaterial material, inout ReflectedLight reflectedLight ) {

	vec3 irradiance = getGradientIrradiance( geometryNormal, directLight.direction ) * directLight.color;

	reflectedLight.directDiffuse += irradiance * BRDF_Lambert( material.diffuseColor );

	reflectedLight.directSpecular += irradiance * BRDF_BlinnPhong( directLight.direction, geometryViewDir, geometryNormal, material.specularColor, material.specularShininess ) * material.specularStrength;

}

void RE_IndirectDiffuse_BlinnPhong( const in vec3 irradiance, const in vec3 geometryPosition, const in vec3 geometryNormal, const in vec3 geometryViewDir, const in vec3 geometryClearcoatNormal, const in BlinnPhongMaterial material, inout ReflectedLight reflectedLight ) {

	reflectedLight.indirectDiffuse += irradiance * BRDF_Lambert( material.diffuseColor );

}

#define RE_Direct				RE_Direct_BlinnPhong
#define RE_IndirectDiffuse		RE_IndirectDiffuse_BlinnPhong
`;
const mmd_toon_matcap_fragment = `
#ifdef USE_MATCAP

	vec3 viewDir = normalize( vViewPosition );
	vec3 x = normalize( vec3( viewDir.z, 0.0, - viewDir.x ) );
	vec3 y = cross( viewDir, x );
	vec2 uv = vec2( dot( x, normal ), dot( y, normal ) ) * 0.495 + 0.5; // 0.495 to remove artifacts caused by undersized matcap disks
	vec4 matcapColor = texture2D( matcap, uv );

	#ifdef MATCAP_BLENDING_MULTIPLY

		outgoingLight *= matcapColor.rgb;

	#elif defined( MATCAP_BLENDING_ADD )

		outgoingLight += matcapColor.rgb;

	#endif

#endif
`;
const MMDToonShader = {
	defines: {
		MATCAP: true,
		MATCAP_BLENDING_ADD: true,
		TOON: true
	},
	fragmentShader: ShaderLib.phong.fragmentShader.replace("#include <common>", `
					#ifdef USE_MATCAP
						uniform sampler2D matcap;
					#endif

					#include <common>
				`).replace("#include <envmap_common_pars_fragment>", `
					#include <gradientmap_pars_fragment>
				`).replace("#include <envmap_pars_fragment>", "").replace("#include <lights_phong_pars_fragment>", lights_mmd_toon_pars_fragment).replace("#include <envmap_fragment>", `
					${mmd_toon_matcap_fragment}
				`),
	name: "MMDToonShader",
	uniforms: UniformsUtils.merge([
		ShaderLib.toon.uniforms,
		ShaderLib.phong.uniforms,
		ShaderLib.matcap.uniforms
	]),
	vertexShader: ShaderLib.phong.vertexShader.replace("#include <envmap_pars_vertex>", "").replace("#include <envmap_vertex>", "")
};

//#endregion
//#region src/materials/mmd-toon-material.ts
var MMDToonMaterial = class extends ShaderMaterial {
	get matcapCombine() {
		return this._matcapCombine;
	}
	set matcapCombine(value) {
		this._matcapCombine = value;
		switch (value) {
			case MultiplyOperation:
				this.defines.MATCAP_BLENDING_MULTIPLY = true;
				delete this.defines.MATCAP_BLENDING_ADD;
				break;
			case AddOperation:
			default:
				this.defines.MATCAP_BLENDING_ADD = true;
				delete this.defines.MATCAP_BLENDING_MULTIPLY;
				break;
		}
	}
	get shininess() {
		return this._shininess;
	}
	set shininess(value) {
		this._shininess = value;
		this.uniforms.shininess.value = Math.max(this._shininess, 1e-4);
	}
	constructor(parameters) {
		super();
		this.isMMDToonMaterial = true;
		this.type = "MMDToonMaterial";
		this._matcapCombine = AddOperation;
		this._shininess = 30;
		this.emissiveIntensity = 1;
		this.normalMapType = TangentSpaceNormalMap;
		this.combine = MultiplyOperation;
		this.wireframeLinecap = "round";
		this.wireframeLinejoin = "round";
		this.flatShading = false;
		this.lights = true;
		this.vertexShader = MMDToonShader.vertexShader;
		this.fragmentShader = MMDToonShader.fragmentShader;
		this.defines = Object.assign({}, MMDToonShader.defines);
		this.uniforms = UniformsUtils.clone(MMDToonShader.uniforms);
		for (const propertyName of [
			"specular",
			"opacity",
			"diffuse",
			"map",
			"matcap",
			"gradientMap",
			"lightMap",
			"lightMapIntensity",
			"aoMap",
			"aoMapIntensity",
			"emissive",
			"emissiveMap",
			"bumpMap",
			"bumpScale",
			"normalMap",
			"normalScale",
			"displacementBias",
			"displacementMap",
			"displacementScale",
			"specularMap",
			"alphaMap",
			"reflectivity",
			"refractionRatio"
		]) Object.defineProperty(this, propertyName, {
			get() {
				return this.uniforms[propertyName].value;
			},
			set(value) {
				this.uniforms[propertyName].value = value;
			}
		});
		Object.defineProperty(this, "color", Object.getOwnPropertyDescriptor(this, "diffuse"));
		this.setValues(parameters);
	}
	copy(source) {
		super.copy(source);
		this._matcapCombine = source._matcapCombine;
		this._shininess = source._shininess;
		this.emissiveIntensity = source.emissiveIntensity;
		this.normalMapType = source.normalMapType;
		this.combine = source.combine;
		this.wireframeLinecap = source.wireframeLinecap;
		this.wireframeLinejoin = source.wireframeLinejoin;
		this.flatShading = source.flatShading;
		return this;
	}
};

//#endregion
//#region ../../node_modules/.pnpm/babylon-mmd@1.0.0_@babylonjs+core@8.29.0/node_modules/babylon-mmd/esm/Loader/sharedToonTextures.js
/**
* Toon texture that exists as a kind of constant
*/
var SharedToonTextures = class {
	/**
	* Shared toon textures data (base64)
	*/
	static Data = [
		"data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAL0lEQVRYR+3QQREAAAzCsOFfNJPBJ1XQS9r2hsUAAQIECBAgQIAAAQIECBAgsBZ4MUx/ofm2I/kAAAAASUVORK5CYII=",
		"data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAN0lEQVRYR+3WQREAMBACsZ5/bWiiMvgEBTt5cW37hjsBBAgQIECAwFwgyfYPCCBAgAABAgTWAh8aBHZBl14e8wAAAABJRU5ErkJggg==",
		"data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAOUlEQVRYR+3WMREAMAwDsYY/yoDI7MLwIiP40+RJklfcCCBAgAABAgTqArfb/QMCCBAgQIAAgbbAB3z/e0F3js2cAAAAAElFTkSuQmCC",
		"data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAN0lEQVRYR+3WQREAMBACsZ5/B5ilMvgEBTt5cW37hjsBBAgQIECAwFwgyfYPCCBAgAABAgTWAh81dWyx0gFwKAAAAABJRU5ErkJggg==",
		"data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAOklEQVRYR+3WoREAMAwDsWb/UQtCy9wxTOQJ/oQ8SXKKGwEECBAgQIBAXeDt7f4BAQQIECBAgEBb4AOz8Hzx7WLY4wAAAABJRU5ErkJggg==",
		"data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAABPUlEQVRYR+1XwW7CMAy1+f9fZOMysSEOEweEOPRNdm3HbdOyIhAcklPrOs/PLy9RygBALxzcCDQFmgJNgaZAU6Ap0BR4PwX8gsRMVLssMRH5HcpzJEaWL7EVg9F1IHRlyqQohgVr4FGUlUcMJSjcUlDw0zvjeun70cLWmneoyf7NgBTQSniBTQQSuJAZsOnnaczjIMb5hCiuHKxokCrJfVnrctyZL0PkJAJe1HMil4nxeyi3Ypfn1kX51jpPvo/JeCNC4PhVdHdJw2XjBR8brF8PEIhNVn12AgP7uHsTBguBn53MUZCqv7Lp07Pn5k1Ro+uWmUNn7D+M57rtk7aG0Vo73xyF/fbFf0bPJjDXngnGocDTdFhygZjwUQrMNrDcmZlQT50VJ/g/UwNyHpu778+yW+/ksOz/BFo54P4AsUXMfRq7XWsAAAAASUVORK5CYII=",
		"data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAACMElEQVRYR+2Xv4pTQRTGf2dubhLdICiii2KnYKHVolhauKWPoGAnNr6BD6CvIVaihYuI2i1ia0BY0MZGRHQXjZj/mSPnnskfNWiWZUlzJ5k7M2cm833nO5Mziej2DWWJRUoCpQKlAntSQCqgw39/iUWAGmh37jrRnVsKlgpiqmkoGVABA7E57fvY+pJDdgKqF6HzFCSADkDq+F6AHABtQ+UMVE5D7zXod7fFNhTEckTbj5XQgHzNN+5tQvc5NG7C6BNkp6D3EmpXHDR+dQAjFLchW3VS9rlw3JBh+B7ys5Cf9z0GW1C/7P32AyBAOAz1q4jGliIH3YPuBnSfQX4OGreTIgEYQb/pBDtPnEQ4CivXYPAWBk13oHrB54yA9QuSn2H4AcKRpEILDt0BUzj+RLR1V5EqjD66NPRBVpLcQwjHoHYJOhsQv6U4mnzmrIXJCFr4LDwm/xBUoboG9XX4cc9VKdYoSA2yk5NQLJaKDUjTBoveG3Z2TElTxwjNK4M3LEZgUdDdruvcXzKBpStgp2NPiWi3ks9ZXxIoFVi+AvHLdc9TqtjL3/aYjpPlrzOcEnK62Szhimdd7xX232zFDTgtxezOu3WNMRLjiKgjtOhHVMd1loynVHvOgjuIIJMaELEqhJAV/RCSLbWTcfPFakFgFlALTRRvx+ok6Hlp/Q+v3fmx90bMyUzaEAhmM3KvHlXTL5DxnbGf/1M8RNNACLL5MNtPxP/mypJAqcDSFfgFhpYqWUzhTEAAAAAASUVORK5CYII=",
		"data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAL0lEQVRYR+3QQREAAAzCsOFfNJPBJ1XQS9r2hsUAAQIECBAgQIAAAQIECBAgsBZ4MUx/ofm2I/kAAAAASUVORK5CYII=",
		"data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAL0lEQVRYR+3QQREAAAzCsOFfNJPBJ1XQS9r2hsUAAQIECBAgQIAAAQIECBAgsBZ4MUx/ofm2I/kAAAAASUVORK5CYII=",
		"data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAL0lEQVRYR+3QQREAAAzCsOFfNJPBJ1XQS9r2hsUAAQIECBAgQIAAAQIECBAgsBZ4MUx/ofm2I/kAAAAASUVORK5CYII=",
		"data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAL0lEQVRYR+3QQREAAAzCsOFfNJPBJ1XQS9r2hsUAAQIECBAgQIAAAQIECBAgsBZ4MUx/ofm2I/kAAAAASUVORK5CYII="
	];
};

//#endregion
//#region src/utils/build-material/types.ts
const NON_ALPHA_CHANNEL_FORMATS = [
	RGB_S3TC_DXT1_Format,
	RGB_PVRTC_4BPPV1_Format,
	RGB_PVRTC_2BPPV1_Format,
	RGB_ETC1_Format,
	RGB_ETC2_Format
];

//#endregion
//#region src/utils/build-material/utils.ts
const checkImageTransparency = (map, geometry, groupIndex) => {
	map.readyCallbacks.push((texture) => {
		const createImageData = (image) => {
			const canvas = document.createElement("canvas");
			canvas.width = image.width;
			canvas.height = image.height;
			const context = canvas.getContext("2d");
			context.drawImage(image, 0, 0);
			return context.getImageData(0, 0, canvas.width, canvas.height);
		};
		const detectImageTransparency = (image, uvs, indices) => {
			const width = image.width;
			const height = image.height;
			const data = image.data;
			const threshold = 253;
			const getAlphaByUv = (image$1, uv) => {
				const width$1 = image$1.width;
				const height$1 = image$1.height;
				let x = Math.round(uv.x * width$1) % width$1;
				let y = Math.round(uv.y * height$1) % height$1;
				if (x < 0) x += width$1;
				if (y < 0) y += height$1;
				const index = y * width$1 + x;
				return image$1.data[index * 4 + 3];
			};
			if (data.length / (width * height) !== 4) return false;
			for (let i = 0; i < indices.length; i += 3) {
				const centerUV = {
					x: 0,
					y: 0
				};
				for (let j = 0; j < 3; j++) {
					const index = indices[i * 3 + j];
					const uv = {
						x: uvs[index * 2 + 0],
						y: uvs[index * 2 + 1]
					};
					if (getAlphaByUv(image, uv) < threshold) return true;
					centerUV.x += uv.x;
					centerUV.y += uv.y;
				}
				centerUV.x /= 3;
				centerUV.y /= 3;
				if (getAlphaByUv(image, centerUV) < threshold) return true;
			}
			return false;
		};
		if ("isCompressedTexture" in texture && texture.isCompressedTexture === true) {
			if (NON_ALPHA_CHANNEL_FORMATS.includes(texture.format)) map.transparent = false;
			else map.transparent = true;
			return;
		}
		const imageData = "data" in texture.image && texture.image.data != null ? texture.image : createImageData(texture.image);
		const group = geometry.groups[groupIndex];
		if (detectImageTransparency(imageData, geometry.attributes.uv.array, geometry.index.array.slice(group.start, group.start + group.count))) map.transparent = true;
	});
};
const getRotatedImage = (image) => {
	const canvas = document.createElement("canvas");
	const context = canvas.getContext("2d");
	const width = image.width;
	const height = image.height;
	canvas.width = width;
	canvas.height = height;
	context.clearRect(0, 0, width, height);
	context.translate(width / 2, height / 2);
	context.rotate(.5 * Math.PI);
	context.translate(-width / 2, -height / 2);
	context.drawImage(image, 0, 0);
	return context.getImageData(0, 0, width, height);
};
const loadTextureResource = (filePath, ctx, params = {}) => {
	let fullPath;
	if (params.isDefaultToonTexture === true) {
		let index;
		try {
			index = Number.parseInt(/toon(\d{2})\.bmp$/.exec(filePath)[1]);
		} catch {
			console.warn(`MMDLoader: ${filePath} seems like a not right default texture path. Using toon00.bmp instead.`);
			index = 0;
		}
		fullPath = SharedToonTextures.Data[index];
	} else fullPath = LoaderUtils.resolveURL(filePath, ctx.resourcePath);
	if (ctx.textures[fullPath] != null) return ctx.textures[fullPath];
	let loader = ctx.manager.getHandler(fullPath);
	if (loader === null) loader = filePath.slice(-4).toLowerCase() === ".tga" ? ctx.getTGALoader() : ctx.textureLoader;
	const texture = loader.load(fullPath, (t) => {
		if (params.isToonTexture === true) {
			t.image = getRotatedImage(t.image);
			t.magFilter = NearestFilter;
			t.minFilter = NearestFilter;
			t.generateMipmaps = false;
		}
		t.flipY = false;
		t.wrapS = RepeatWrapping;
		t.wrapT = RepeatWrapping;
		t.colorSpace = SRGBColorSpace;
		for (let i = 0; i < texture.readyCallbacks.length; i++) texture.readyCallbacks[i](texture);
		delete texture.readyCallbacks;
	}, ctx.onProgress, ctx.onError);
	texture.readyCallbacks = [];
	ctx.textures[fullPath] = texture;
	return texture;
};

//#endregion
//#region src/utils/build-material/index.ts
const mapPmxToParams = (material, pmxTextures, geometry, ctx, groupIndex) => {
	const params = { userData: { MMD: {} } };
	params.name = material.name;
	params.diffuse = new Color().setRGB(material.diffuse[0], material.diffuse[1], material.diffuse[2], SRGBColorSpace);
	params.opacity = material.diffuse[3];
	params.specular = new Color().setRGB(...material.specular, SRGBColorSpace);
	params.shininess = material.shininess;
	params.emissive = new Color().setRGB(...material.ambient, SRGBColorSpace);
	params.transparent = params.opacity !== 1;
	params.fog = true;
	params.blending = CustomBlending;
	params.blendSrc = SrcAlphaFactor;
	params.blendDst = OneMinusSrcAlphaFactor;
	params.blendSrcAlpha = SrcAlphaFactor;
	params.blendDstAlpha = DstAlphaFactor;
	if ((material.flag & PmxObject.Material.Flag.IsDoubleSided) === 1) params.side = DoubleSide;
	else params.side = params.opacity === 1 ? FrontSide : DoubleSide;
	if (material.textureIndex !== -1) {
		params.map = loadTextureResource(pmxTextures[material.textureIndex], ctx);
		params.userData.MMD.mapFileName = pmxTextures[material.textureIndex];
	}
	if (material.sphereTextureIndex !== -1 && (material.sphereTextureMode === PmxObject.Material.SphereTextureMode.Multiply || material.sphereTextureMode === PmxObject.Material.SphereTextureMode.Add)) {
		params.matcap = loadTextureResource(pmxTextures[material.sphereTextureIndex], ctx);
		params.userData.MMD.matcapFileName = pmxTextures[material.sphereTextureIndex];
		params.matcapCombine = material.sphereTextureMode === PmxObject.Material.SphereTextureMode.Multiply ? MultiplyOperation : AddOperation;
	}
	let isDefaultToon, toonFileName;
	if (material.isSharedToonTexture || material.toonTextureIndex === -1) {
		toonFileName = `toon${`0${material.toonTextureIndex + 1}`.slice(-2)}.bmp`;
		isDefaultToon = true;
	} else {
		toonFileName = pmxTextures[material.toonTextureIndex];
		isDefaultToon = false;
	}
	params.gradientMap = loadTextureResource(toonFileName, ctx, {
		isDefaultToonTexture: isDefaultToon,
		isToonTexture: true
	});
	params.userData.outlineParameters = {
		alpha: material.edgeColor[3],
		color: material.edgeColor.slice(0, 3),
		thickness: material.edgeSize / 300,
		visible: (material.flag & PmxObject.Material.Flag.EnabledToonEdge) !== 0 && material.edgeSize > 0
	};
	if (params.map !== void 0) {
		if (!params.transparent) checkImageTransparency(params.map, geometry, groupIndex);
		params.emissive.multiplyScalar(.2);
	}
	return params;
};
const createMMDMaterial = (params) => {
	return new MMDToonMaterial(params);
};
const applyMorphTransparencyFix = (materials, morphs) => {
	const checkAlphaMorph = (elements, targetMaterials) => {
		for (let i = 0, il = elements.length; i < il; i++) {
			const element = elements[i];
			if (element.index === -1) continue;
			const material = targetMaterials[element.index];
			if (material.opacity !== element.diffuse[3]) material.transparent = true;
		}
	};
	for (let i = 0, il = morphs.length; i < il; i++) {
		const morph = morphs[i];
		if (morph.type === PmxObject.Morph.Type.GroupMorph) for (let j = 0, jl = morph.indices.length; j < jl; j++) {
			const morph2 = morphs[morph.indices[j]];
			if (morph2.type !== PmxObject.Morph.Type.MaterialMorph) continue;
			checkAlphaMorph(morph2.elements, materials);
		}
		else if (morph.type === PmxObject.Morph.Type.MaterialMorph) checkAlphaMorph(morph.elements, materials);
	}
};
const renderStyleRegistry = /* @__PURE__ */ new Map();
const getRenderStyleRegistry = () => {
	if (renderStyleRegistry.size === 0) renderStyleRegistry.set("default", (params) => {
		return createMMDMaterial(params);
	});
	return renderStyleRegistry;
};
const buildMaterial = (data, geometry, resourcePath, customManager, renderStyle = "default", onProgress, onError) => {
	const manager = customManager ?? DefaultLoadingManager;
	const textureLoader = new TextureLoader(manager);
	textureLoader.setCrossOrigin("anonymous");
	let tgaLoader;
	const getTGALoader = () => {
		if (tgaLoader === void 0) tgaLoader = new TGALoader(manager);
		return tgaLoader;
	};
	const ctx = {
		getTGALoader,
		manager,
		onError,
		onProgress,
		resourcePath,
		textureLoader,
		textures: {}
	};
	const materials = [];
	const registry = getRenderStyleRegistry();
	const setMaterial = registry.get(renderStyle) ?? registry.get("default");
	for (let i = 0; i < data.materials.length; i++) {
		const pmxMaterial = data.materials[i];
		const params = mapPmxToParams(pmxMaterial, data.textures, geometry, ctx, i);
		materials.push(setMaterial(params));
	}
	applyMorphTransparencyFix(materials, data.morphs);
	return materials;
};

//#endregion
//#region src/utils/build-mesh.ts
/** @experimental */
const buildMesh = (geometry, materials) => {
	return new SkinnedMesh(geometry, materials);
};

//#endregion
//#region src/utils/post-parse.ts
/**
* Post-PMX parse normalization: flip Z to right-handed coords once so downstream builders
* (geometry/bones/morphs/rigid bodies) don't need scattered Z flips.
*/
const postParseProcessing = (pmx) => {
	pmx.vertices.forEach((v) => {
		v.position[2] = -v.position[2];
		v.normal[2] = -v.normal[2];
	});
	for (let i = 0; i < pmx.indices.length; i += 3) {
		const tmp = pmx.indices[i + 1];
		pmx.indices[i + 1] = pmx.indices[i + 2];
		pmx.indices[i + 2] = tmp;
	}
	pmx.bones.forEach((bone) => {
		bone.position[2] = -bone.position[2];
	});
	pmx.morphs.forEach((morph) => {
		if (morph.type !== PmxObject.Morph.Type.VertexMorph) return;
		for (let i = 0; i < morph.positions.length; i += 3) morph.positions[i + 2] = -morph.positions[i + 2];
	});
	pmx.rigidBodies?.forEach((body) => {
		body.shapePosition[2] = -body.shapePosition[2];
		body.shapeRotation[0] = -body.shapeRotation[0];
		body.shapeRotation[1] = -body.shapeRotation[1];
	});
	pmx.joints?.forEach((joint) => {
		joint.position[2] = -joint.position[2];
		joint.rotation[0] = -joint.rotation[0];
		joint.rotation[1] = -joint.rotation[1];
	});
	return pmx;
};

//#endregion
//#region src/loaders/loader-deps.ts
const defaultDeps = {
	buildBones,
	buildGeometry,
	buildGrants,
	buildIK,
	buildMaterials: (pmx, geo, rp, manager) => buildMaterial(pmx, geo, rp, manager),
	buildMesh,
	postParseProcessing
};
const resolveDeps = (plugins = [], baseDeps = defaultDeps) => {
	let mergedDeps = { ...baseDeps };
	plugins.forEach((p) => {
		mergedDeps = {
			...mergedDeps,
			...p(mergedDeps)
		};
	});
	return mergedDeps;
};

//#endregion
//#region src/loaders/mmd-loader.ts
/** @experimental */
var MMDLoader = class extends Loader {
	constructor(plugins = [], manager) {
		super(manager);
		this.plugins = [];
		this.plugins.push(...plugins);
	}
	load(url, onLoad, onProgress, onError) {
		let resourcePath;
		if (this.resourcePath !== "") resourcePath = this.resourcePath;
		else if (this.path !== "") resourcePath = LoaderUtils.resolveURL(LoaderUtils.extractUrlBase(url), this.path);
		else resourcePath = LoaderUtils.extractUrlBase(url);
		const buildDeps = this.getResolvedDeps();
		const loader = new FileLoader(this.manager);
		loader.setResponseType("arraybuffer");
		loader.setPath(this.path);
		loader.setRequestHeader(this.requestHeader);
		loader.setWithCredentials(this.withCredentials);
		loader.load(url, (buffer) => {
			try {
				const modelExtension = extractModelExtension(buffer);
				if (!["pmd", "pmx"].includes(modelExtension)) {
					onError?.(/* @__PURE__ */ new Error(`MMDLoader: Unknown model file extension .${modelExtension}.`));
					return;
				}
				(modelExtension === "pmd" ? PmdReader : PmxReader).ParseAsync(buffer).then((pmx) => onLoad(this.assembleMMD(pmx, resourcePath, buildDeps))).catch(onError);
			} catch (e) {
				onError?.(e);
			}
		}, onProgress, onError);
	}
	async loadAsync(url, onProgress) {
		return super.loadAsync(url, onProgress);
	}
	register(plugin) {
		this.plugins.push(plugin);
		return this;
	}
	assembleMMD(pmx, resourcePath, deps = defaultDeps) {
		const { buildBones: buildBones$1, buildGeometry: buildGeometry$1, buildGrants: buildGrants$1, buildIK: buildIK$1, buildMaterials, buildMesh: buildMesh$1, postParseProcessing: postParseProcessing$1 } = deps;
		pmx = postParseProcessing$1(pmx);
		const geometry = buildGeometry$1(pmx);
		const rawMesh = buildMesh$1(geometry, buildMaterials(pmx, geometry, resourcePath, this.manager));
		const skinnedMesh = buildBones$1(pmx, rawMesh);
		const grants = buildGrants$1(pmx);
		const iks = buildIK$1(pmx);
		return new MMD(pmx, skinnedMesh, grants, iks);
	}
	getResolvedDeps() {
		return resolveDeps(this.plugins, defaultDeps);
	}
};

//#endregion
//#region src/loaders/mmd-mesh-loader.ts
/** @experimental */
var MMDMeshLoader = class extends Loader {
	constructor(plugins = [], manager) {
		super(manager);
		this.plugins = [];
		this.plugins.push(...plugins);
	}
	load(url, onLoad, onProgress, onError) {
		let resourcePath;
		if (this.resourcePath !== "") resourcePath = this.resourcePath;
		else if (this.path !== "") resourcePath = LoaderUtils.resolveURL(LoaderUtils.extractUrlBase(url), this.path);
		else resourcePath = LoaderUtils.extractUrlBase(url);
		const buildDeps = this.getResolvedDeps();
		const loader = new FileLoader(this.manager);
		loader.setResponseType("arraybuffer");
		loader.setPath(this.path);
		loader.setRequestHeader(this.requestHeader);
		loader.setWithCredentials(this.withCredentials);
		loader.load(url, (buffer) => {
			try {
				const modelExtension = extractModelExtension(buffer);
				if (!["pmd", "pmx"].includes(modelExtension)) {
					onError?.(/* @__PURE__ */ new Error(`MMDMeshLoader: Unknown model file extension .${modelExtension}.`));
					return;
				}
				(modelExtension === "pmd" ? PmdReader : PmxReader).ParseAsync(buffer).then((pmx) => onLoad(this.assembleMesh(pmx, resourcePath, buildDeps))).catch(onError);
			} catch (e) {
				onError?.(e);
			}
		}, onProgress, onError);
	}
	async loadAsync(url, onProgress) {
		return super.loadAsync(url, onProgress);
	}
	register(plugin) {
		this.plugins.push(plugin);
		return this;
	}
	assembleMesh(pmx, resourcePath, deps = defaultDeps) {
		const { buildBones: buildBones$1, buildGeometry: buildGeometry$1, buildMaterials, buildMesh: buildMesh$1, postParseProcessing: postParseProcessing$1 } = deps;
		pmx = postParseProcessing$1(pmx);
		const geometry = buildGeometry$1(pmx);
		const rawMesh = buildMesh$1(geometry, buildMaterials(pmx, geometry, resourcePath, this.manager));
		return buildBones$1(pmx, rawMesh);
	}
	getResolvedDeps() {
		return resolveDeps(this.plugins, defaultDeps);
	}
};

//#endregion
//#region ../../node_modules/.pnpm/babylon-mmd@1.0.0_@babylonjs+core@8.29.0/node_modules/babylon-mmd/esm/Loader/Parser/vmdObject.js
/**
* VMD data
*
* The creation of this object means that the validation and indexing of the Vmd data are finished
*
* Therefore, there is no parsing error when reading data from VmdData
*/
var VmdData = class VmdData {
	static _Signature = "Vocaloid Motion Data 0002";
	/**
	* Signature bytes
	*
	* The first 30 bytes of the VMD file must be "Vocaloid Motion Data 0002"
	* @internal
	*/
	static SignatureBytes = 30;
	/**
	* Model name bytes
	*
	* The next 20 bytes of the VMD file must be the model name
	*
	* MMD assuming that motion is usually valid for one model
	*
	* so when binding target model name is different from the model name in VMD file, MMD warns the user
	* @internal
	*/
	static ModelNameBytes = 20;
	/**
	* Bone key frame bytes
	* @internal
	*/
	static BoneKeyFrameBytes = 111;
	/**
	* Morph key frame bytes
	* @internal
	*/
	static MorphKeyFrameBytes = 23;
	/**
	* Camera key frame bytes
	* @internal
	*/
	static CameraKeyFrameBytes = 61;
	/**
	* Light key frame bytes
	* @internal
	*/
	static LightKeyFrameBytes = 28;
	/**
	* Self shadow key frame bytes
	* @internal
	*/
	static SelfShadowKeyFrameBytes = 9;
	/**
	* Property key frame bytes
	* @internal
	*/
	static PropertyKeyFrameBytes = 5;
	/**
	* Property key frame IK state bytes
	* @internal
	*/
	static PropertyKeyFrameIkStateBytes = 21;
	/**
	* Data deserializer for reading VMD data
	* @internal
	*/
	dataDeserializer;
	/**
	* Bone key frame count
	*/
	boneKeyFrameCount;
	/**
	* Morph key frame count
	*/
	morphKeyFrameCount;
	/**
	* Camera key frame count
	*/
	cameraKeyFrameCount;
	/**
	* Light key frame count
	*/
	lightKeyFrameCount;
	/**
	* Self shadow key frame count
	*/
	selfShadowKeyFrameCount;
	/**
	* Property key frame count
	*/
	propertyKeyFrameCount;
	constructor(dataDeserializer, boneKeyFrameCount, morphKeyFrameCount, cameraKeyFrameCount, lightKeyFrameCount, selfShadowKeyFrameCount, propertyKeyFrameCount) {
		this.dataDeserializer = dataDeserializer;
		this.boneKeyFrameCount = boneKeyFrameCount;
		this.morphKeyFrameCount = morphKeyFrameCount;
		this.cameraKeyFrameCount = cameraKeyFrameCount;
		this.lightKeyFrameCount = lightKeyFrameCount;
		this.selfShadowKeyFrameCount = selfShadowKeyFrameCount;
		this.propertyKeyFrameCount = propertyKeyFrameCount;
	}
	/**
	* Create a new `VmdData` instance from the given buffer
	* @param buffer ArrayBuffer
	* @param logger Logger
	* @returns `VmdData` instance if the given buffer is a valid VMD data, otherwise `null`
	*/
	static CheckedCreate(buffer, logger = new ConsoleLogger()) {
		const dataDeserializer = new MmdDataDeserializer(buffer);
		dataDeserializer.initializeTextDecoder("shift-jis");
		if (dataDeserializer.bytesAvailable < VmdData.SignatureBytes + VmdData.ModelNameBytes) return null;
		if (dataDeserializer.getSignatureString(this.SignatureBytes).substring(0, this._Signature.length) !== this._Signature) return null;
		dataDeserializer.offset += VmdData.ModelNameBytes;
		let boneKeyFrameCount = 0;
		let morphKeyFrameCount = 0;
		let cameraKeyFrameCount = 0;
		let lightKeyFrameCount = 0;
		let selfShadowKeyFrameCount = 0;
		let propertyKeyFrameCount = 0;
		if (dataDeserializer.bytesAvailable < 4) return null;
		boneKeyFrameCount = dataDeserializer.getUint32();
		if (dataDeserializer.bytesAvailable < boneKeyFrameCount * VmdData.BoneKeyFrameBytes) return null;
		dataDeserializer.offset += boneKeyFrameCount * VmdData.BoneKeyFrameBytes;
		if (dataDeserializer.bytesAvailable < 4) return null;
		morphKeyFrameCount = dataDeserializer.getUint32();
		if (dataDeserializer.bytesAvailable < morphKeyFrameCount * VmdData.MorphKeyFrameBytes) return null;
		dataDeserializer.offset += morphKeyFrameCount * VmdData.MorphKeyFrameBytes;
		if (dataDeserializer.bytesAvailable !== 0) {
			if (dataDeserializer.bytesAvailable < 4) return null;
			cameraKeyFrameCount = dataDeserializer.getUint32();
			if (dataDeserializer.bytesAvailable < cameraKeyFrameCount * VmdData.CameraKeyFrameBytes) return null;
			dataDeserializer.offset += cameraKeyFrameCount * VmdData.CameraKeyFrameBytes;
			if (dataDeserializer.bytesAvailable < 4) return null;
			lightKeyFrameCount = dataDeserializer.getUint32();
			if (dataDeserializer.bytesAvailable < lightKeyFrameCount * VmdData.LightKeyFrameBytes) return null;
			dataDeserializer.offset += lightKeyFrameCount * VmdData.LightKeyFrameBytes;
		}
		if (dataDeserializer.bytesAvailable !== 0) {
			if (dataDeserializer.bytesAvailable < 4) return null;
			selfShadowKeyFrameCount = dataDeserializer.getUint32();
			if (dataDeserializer.bytesAvailable < selfShadowKeyFrameCount * VmdData.SelfShadowKeyFrameBytes) return null;
			dataDeserializer.offset += selfShadowKeyFrameCount * VmdData.SelfShadowKeyFrameBytes;
		}
		if (dataDeserializer.bytesAvailable !== 0) {
			if (dataDeserializer.bytesAvailable < 4) return null;
			propertyKeyFrameCount = dataDeserializer.getUint32();
			for (let i = 0; i < propertyKeyFrameCount; ++i) {
				if (dataDeserializer.bytesAvailable < VmdData.PropertyKeyFrameBytes) return null;
				dataDeserializer.offset += VmdData.PropertyKeyFrameBytes;
				if (dataDeserializer.bytesAvailable < 4) return null;
				const propertyKeyFrameIkStateCount = dataDeserializer.getUint32();
				if (dataDeserializer.bytesAvailable < propertyKeyFrameIkStateCount * VmdData.PropertyKeyFrameIkStateBytes) return null;
				dataDeserializer.offset += propertyKeyFrameIkStateCount * VmdData.PropertyKeyFrameIkStateBytes;
			}
		}
		if (dataDeserializer.bytesAvailable > 0) logger.warn(`There are ${dataDeserializer.bytesAvailable} bytes left after parsing`);
		dataDeserializer.offset = 0;
		return new VmdData(dataDeserializer, boneKeyFrameCount, morphKeyFrameCount, cameraKeyFrameCount, lightKeyFrameCount, selfShadowKeyFrameCount, propertyKeyFrameCount);
	}
};
/**
* VMD object
*
* Lazy parsed VMD data object
*
* The total amount of memory used is more than parsing at once
*
* but you can adjust the instantaneous memory usage to a smaller extent
*/
var VmdObject = class VmdObject {
	/**
	* Property key frames
	*
	* Property key frames are only preparsed because they size is not fixed
	*/
	propertyKeyFrames;
	_vmdData;
	constructor(vmdData, propertyKeyFrames) {
		this._vmdData = vmdData;
		this.propertyKeyFrames = propertyKeyFrames;
	}
	/**
	* Parse VMD data
	* @param vmdData VMD data
	* @returns `VmdObject` instance
	*/
	static Parse(vmdData) {
		const dataDeserializer = vmdData.dataDeserializer;
		const propertyKeyFrames = [];
		dataDeserializer.offset = VmdData.SignatureBytes + VmdData.ModelNameBytes + 4 + vmdData.boneKeyFrameCount * VmdData.BoneKeyFrameBytes + 4 + vmdData.morphKeyFrameCount * VmdData.MorphKeyFrameBytes + 4 + vmdData.cameraKeyFrameCount * VmdData.CameraKeyFrameBytes + 4 + vmdData.lightKeyFrameCount * VmdData.LightKeyFrameBytes + 4 + vmdData.selfShadowKeyFrameCount * VmdData.SelfShadowKeyFrameBytes + 4;
		const propertyKeyFrameCount = vmdData.propertyKeyFrameCount;
		for (let i = 0; i < propertyKeyFrameCount; ++i) {
			const frameNumber = dataDeserializer.getUint32();
			const visible = dataDeserializer.getUint8() !== 0;
			const ikStateCount = dataDeserializer.getUint32();
			const ikStates = [];
			for (let j = 0; j < ikStateCount; ++j) {
				const ikName = dataDeserializer.getDecoderString(20, true);
				const ikEnabled = dataDeserializer.getUint8() !== 0;
				ikStates.push([ikName, ikEnabled]);
			}
			const propertyKeyFrame = {
				frameNumber,
				visible,
				ikStates
			};
			propertyKeyFrames.push(propertyKeyFrame);
		}
		return new VmdObject(vmdData, propertyKeyFrames);
	}
	/**
	* Parse VMD data from the given buffer
	* @param buffer ArrayBuffer
	* @returns `VmdObject` instance
	* @throws {Error} if the given buffer is not a valid VMD data
	*/
	static ParseFromBuffer(buffer) {
		const vmdData = VmdData.CheckedCreate(buffer);
		if (vmdData === null) throw new Error("Invalid VMD data");
		return VmdObject.Parse(vmdData);
	}
	/**
	* Get bone key frame reader
	*/
	get boneKeyFrames() {
		const offset = VmdData.SignatureBytes + VmdData.ModelNameBytes + 4;
		return new VmdObject.BoneKeyFrames(this._vmdData.dataDeserializer, offset, this._vmdData.boneKeyFrameCount);
	}
	/**
	* Get morph key frame reader
	*/
	get morphKeyFrames() {
		const offset = VmdData.SignatureBytes + VmdData.ModelNameBytes + 4 + this._vmdData.boneKeyFrameCount * VmdData.BoneKeyFrameBytes + 4;
		return new VmdObject.MorphKeyFrames(this._vmdData.dataDeserializer, offset, this._vmdData.morphKeyFrameCount);
	}
	/**
	* Get camera key frame reader
	*/
	get cameraKeyFrames() {
		const offset = VmdData.SignatureBytes + VmdData.ModelNameBytes + 4 + this._vmdData.boneKeyFrameCount * VmdData.BoneKeyFrameBytes + 4 + this._vmdData.morphKeyFrameCount * VmdData.MorphKeyFrameBytes + 4;
		return new VmdObject.CameraKeyFrames(this._vmdData.dataDeserializer, offset, this._vmdData.cameraKeyFrameCount);
	}
	/**
	* Get light key frame reader
	*/
	get lightKeyFrames() {
		const offset = VmdData.SignatureBytes + VmdData.ModelNameBytes + 4 + this._vmdData.boneKeyFrameCount * VmdData.BoneKeyFrameBytes + 4 + this._vmdData.morphKeyFrameCount * VmdData.MorphKeyFrameBytes + 4 + this._vmdData.cameraKeyFrameCount * VmdData.CameraKeyFrameBytes + 4;
		return new VmdObject.LightKeyFrames(this._vmdData.dataDeserializer, offset, this._vmdData.lightKeyFrameCount);
	}
	/**
	* Get self shadow key frame reader
	*/
	get selfShadowKeyFrames() {
		const offset = VmdData.SignatureBytes + VmdData.ModelNameBytes + 4 + this._vmdData.boneKeyFrameCount * VmdData.BoneKeyFrameBytes + 4 + this._vmdData.morphKeyFrameCount * VmdData.MorphKeyFrameBytes + 4 + this._vmdData.cameraKeyFrameCount * VmdData.CameraKeyFrameBytes + 4 + this._vmdData.lightKeyFrameCount * VmdData.LightKeyFrameBytes + 4;
		return new VmdObject.SelfShadowKeyFrames(this._vmdData.dataDeserializer, offset, this._vmdData.selfShadowKeyFrameCount);
	}
};
(function(VmdObject$1) {
	/**
	* key frame reader base class
	*/
	class BufferArrayReader {
		_dataDeserializer;
		_startOffset;
		_length;
		/**
		* Create a new `BufferArrayReader` instance
		* @param dataDeserializer Data deserializer
		* @param startOffset Data start offset
		* @param length Data length
		*/
		constructor(dataDeserializer, startOffset, length) {
			this._dataDeserializer = dataDeserializer;
			this._startOffset = startOffset;
			this._length = length;
		}
		/**
		* Length of the data
		*/
		get length() {
			return this._length;
		}
	}
	VmdObject$1.BufferArrayReader = BufferArrayReader;
	/**
	* Bone key frame reader
	*/
	class BoneKeyFrames extends BufferArrayReader {
		/**
		* Create a new `BoneKeyFrames` instance
		* @param dataDeserializer Data deserializer
		* @param startOffset Data start offset
		* @param length Data length
		*/
		constructor(dataDeserializer, startOffset, length) {
			super(dataDeserializer, startOffset, length);
		}
		/**
		* Get the data at the given index
		* @param index Index
		* @returns `BoneKeyFrame` instance
		*/
		get(index) {
			const offset = this._startOffset + index * VmdData.BoneKeyFrameBytes;
			return new BoneKeyFrame(this._dataDeserializer, offset);
		}
	}
	VmdObject$1.BoneKeyFrames = BoneKeyFrames;
	/**
	* Bone key frame
	*/
	class BoneKeyFrame {
		/**
		* Bone name
		*/
		boneName;
		/**
		* Frame number
		*/
		frameNumber;
		/**
		* Position
		*/
		position;
		/**
		* Rotation quaternion
		*/
		rotation;
		/**
		* Interpolation
		*
		* https://hariganep.seesaa.net/article/201103article_1.html
		* https://x.com/KuroNekoMeguMMD/status/1864306974856499520/
		*
		* The interpolation parameters are four Bezier curves (0,0), (x1,y1), (x2,y2), and (127,127)
		*
		* It represents the parameters of each axis
		*
		* - X-axis interpolation parameters (X_x1, X_y1), (X_x2, X_y2)
		* - Y-axis interpolation parameters (Y_x1, Y_y1), (Y_x2, Y_y2)
		* - Z-axis interpolation parameters (Z_x1, Z_y1), (Z_x2, Z_y2)
		* - Rotation interpolation parameters (R_x1, R_y1), (R_x2, R_y2)
		*
		* And interpolation parameters also include physics toggle parameters
		* - Physics toggle parameters (phy1, phy2)
		*
		* Physics toggle parameters has two varients
		* - phy1: 0x00, phy2: 0x00 (physics off)
		* - phy1: 0x63, phy2: 0x0f (physics on)
		*
		* Then, the interpolation parameters are as follows
		*
		* X_x1,Y_x1,phy1,phy2,
		* X_y1,Y_y1,Z_y1,R_y1,
		* X_x2,Y_x2,Z_x2,R_x2,
		* X_y2,Y_y2,Z_y2,R_y2,
		*
		* Y_x1,Z_x1,R_x1,X_y1,
		* Y_y1,Z_y1,R_y1,X_x2,
		* Y_x2,Z_x2,R_x2,X_y2,
		* Y_y2,Z_y2,R_y2, 00,
		*
		* Z_x1,R_x1,X_y1,Y_y1,
		* Z_y1,R_y1,X_x2,Y_x2,
		* Z_x2,R_x2,X_y2,Y_y2,
		* Z_y2,R_y2, 00, 00,
		*
		* R_x1,X_y1,Y_y1,Z_y1,
		* R_y1,X_x2,Y_x2,Z_x2,
		* R_x2,X_y2,Y_y2,Z_y2,
		* R_y2, 00, 00, 00
		*
		* [4][4][4] = [64]
		*/
		interpolation;
		/**
		* Create a new `BoneKeyFrame` instance
		* @param dataDeserializer Data deserializer
		* @param offset Data offset
		*/
		constructor(dataDeserializer, offset) {
			dataDeserializer.offset = offset;
			this.boneName = dataDeserializer.getDecoderString(15, true);
			this.frameNumber = dataDeserializer.getUint32();
			this.position = dataDeserializer.getFloat32Tuple(3);
			this.rotation = dataDeserializer.getFloat32Tuple(4);
			this.interpolation = new Uint8Array(64);
			for (let i = 0; i < 64; ++i) this.interpolation[i] = dataDeserializer.getUint8();
		}
	}
	VmdObject$1.BoneKeyFrame = BoneKeyFrame;
	(function(BoneKeyFramePhysicsInfoKind) {
		/**
		* Physics off
		*
		* Rigid body position is driven by animation
		*/
		BoneKeyFramePhysicsInfoKind[BoneKeyFramePhysicsInfoKind["Off"] = 25359] = "Off";
		/**
		* Physics on
		*
		* Rigid body position is driven by physics, only affected when the bone has a rigid body
		*/
		BoneKeyFramePhysicsInfoKind[BoneKeyFramePhysicsInfoKind["On"] = 0] = "On";
	})(VmdObject$1.BoneKeyFramePhysicsInfoKind || (VmdObject$1.BoneKeyFramePhysicsInfoKind = {}));
	/**
	* Morph key frame reader
	*/
	class MorphKeyFrames extends BufferArrayReader {
		/**
		* Create a new `MorphKeyFrames` instance
		* @param dataDeserializer Data deserializer
		* @param startOffset Data start offset
		* @param length Data length
		*/
		constructor(dataDeserializer, startOffset, length) {
			super(dataDeserializer, startOffset, length);
		}
		/**
		* Get the data at the given index
		* @param index Index
		* @returns `MorphKeyFrame` instance
		*/
		get(index) {
			const offset = this._startOffset + index * VmdData.MorphKeyFrameBytes;
			return new MorphKeyFrame(this._dataDeserializer, offset);
		}
	}
	VmdObject$1.MorphKeyFrames = MorphKeyFrames;
	/**
	* Morph key frame
	*/
	class MorphKeyFrame {
		/**
		* Morph name
		*/
		morphName;
		/**
		* Frame number
		*/
		frameNumber;
		/**
		* Weight
		*/
		weight;
		/**
		* Create a new `MorphKeyFrame` instance
		* @param dataDeserializer Data deserializer
		* @param offset Data offset
		*/
		constructor(dataDeserializer, offset) {
			dataDeserializer.offset = offset;
			this.morphName = dataDeserializer.getDecoderString(15, true);
			this.frameNumber = dataDeserializer.getUint32();
			this.weight = dataDeserializer.getFloat32();
		}
	}
	VmdObject$1.MorphKeyFrame = MorphKeyFrame;
	/**
	* Camera key frame reader
	*/
	class CameraKeyFrames extends BufferArrayReader {
		/**
		* Create a new `CameraKeyFrames` instance
		* @param dataDeserializer Data deserializer
		* @param startOffset Data start offset
		* @param length Data length
		*/
		constructor(dataDeserializer, startOffset, length) {
			super(dataDeserializer, startOffset, length);
		}
		/**
		* Get the data at the given index
		* @param index Index
		* @returns `CameraKeyFrame` instance
		*/
		get(index) {
			const offset = this._startOffset + index * VmdData.CameraKeyFrameBytes;
			return new CameraKeyFrame(this._dataDeserializer, offset);
		}
	}
	VmdObject$1.CameraKeyFrames = CameraKeyFrames;
	/**
	* Camera key frame
	*/
	class CameraKeyFrame {
		/**
		* Frame number
		*/
		frameNumber;
		/**
		* Distance from the camera center
		*/
		distance;
		/**
		* Camera center position
		*/
		position;
		/**
		* Camera rotation in yaw, pitch, roll order
		*/
		rotation;
		/**
		* Interpolation
		*
		* range: 0..=127
		*
		* default linear interpolation is 20, 107, 20, 107
		*
		* Repr:
		*
		* x_ax, x_bx, x_ay, x_by,
		* y_ax, y_bx, y_ay, y_by,
		* z_ax, z_bx, z_ay, z_by,
		* rot_ax, rot_bx, rot_ay, rot_by,
		* distance_ax, distance_bx, distance_ay, distance_by,
		* angle_ax, angle_bx, angle_ay, angle_by
		*/
		interpolation;
		/**
		* Angle of view (in degrees)
		*/
		fov;
		/**
		* Whether the camera is perspective or orthographic
		*/
		perspective;
		/**
		* Create a new `CameraKeyFrame` instance
		* @param dataDeserializer Data deserializer
		* @param offset Data offset
		*/
		constructor(dataDeserializer, offset) {
			dataDeserializer.offset = offset;
			this.frameNumber = dataDeserializer.getUint32();
			this.distance = dataDeserializer.getFloat32();
			this.position = dataDeserializer.getFloat32Tuple(3);
			this.rotation = dataDeserializer.getFloat32Tuple(3);
			this.interpolation = new Uint8Array(24);
			for (let i = 0; i < 24; ++i) this.interpolation[i] = dataDeserializer.getUint8();
			this.fov = dataDeserializer.getUint32();
			this.perspective = dataDeserializer.getUint8() !== 0;
		}
	}
	VmdObject$1.CameraKeyFrame = CameraKeyFrame;
	/**
	* Light key frame reader
	*/
	class LightKeyFrames extends BufferArrayReader {
		/**
		* Create a new `LightKeyFrames` instance
		* @param dataDeserializer Data deserializer
		* @param startOffset Data start offset
		* @param length Data length
		*/
		constructor(dataDeserializer, startOffset, length) {
			super(dataDeserializer, startOffset, length);
		}
		/**
		* Get the data at the given index
		* @param index Index
		* @returns `LightKeyFrame` instance
		*/
		get(index) {
			const offset = this._startOffset + index * VmdData.LightKeyFrameBytes;
			return new LightKeyFrame(this._dataDeserializer, offset);
		}
	}
	VmdObject$1.LightKeyFrames = LightKeyFrames;
	/**
	* Light key frame
	*/
	class LightKeyFrame {
		/**
		* Frame number
		*/
		frameNumber;
		/**
		* Light color
		*/
		color;
		/**
		* Light direction
		*/
		direction;
		/**
		* Create a new `LightKeyFrame` instance
		* @param dataDeserializer Data deserializer
		* @param offset Data offset
		*/
		constructor(dataDeserializer, offset) {
			dataDeserializer.offset = offset;
			this.frameNumber = dataDeserializer.getUint32();
			this.color = dataDeserializer.getFloat32Tuple(3);
			this.direction = dataDeserializer.getFloat32Tuple(3);
		}
	}
	VmdObject$1.LightKeyFrame = LightKeyFrame;
	/**
	* Self shadow key frame reader
	*/
	class SelfShadowKeyFrames extends BufferArrayReader {
		/**
		* Create a new `SelfShadowKeyFrames` instance
		* @param dataDeserializer Data deserializer
		* @param startOffset Data start offset
		* @param length Data length
		*/
		constructor(dataDeserializer, startOffset, length) {
			super(dataDeserializer, startOffset, length);
		}
		/**
		* Get the data at the given index
		* @param index Index
		* @returns `SelfShadowKeyFrame` instance
		*/
		get(index) {
			const offset = this._startOffset + index * VmdData.SelfShadowKeyFrameBytes;
			return new SelfShadowKeyFrame(this._dataDeserializer, offset);
		}
	}
	VmdObject$1.SelfShadowKeyFrames = SelfShadowKeyFrames;
	/**
	* Self shadow key frame
	*/
	class SelfShadowKeyFrame {
		/**
		* Frame number
		*/
		frameNumber;
		/**
		* Shadow mode
		*/
		mode;
		/**
		* Distance
		*/
		distance;
		/**
		* Create a new `SelfShadowKeyFrame` instance
		* @param dataDeserializer Data deserializer
		* @param offset Data offset
		*/
		constructor(dataDeserializer, offset) {
			dataDeserializer.offset = offset;
			this.frameNumber = dataDeserializer.getUint32();
			this.mode = dataDeserializer.getUint8();
			this.distance = dataDeserializer.getFloat32();
		}
	}
	VmdObject$1.SelfShadowKeyFrame = SelfShadowKeyFrame;
})(VmdObject || (VmdObject = {}));

//#endregion
//#region src/loaders/vmd-loader.ts
/** @experimental */
var VMDLoader = class extends Loader {
	constructor(manager) {
		super(manager);
	}
	load(url, onLoad, onProgress, onError) {
		const loader = new FileLoader(this.manager);
		loader.setResponseType("arraybuffer");
		loader.setPath(this.path);
		loader.setRequestHeader(this.requestHeader);
		loader.setWithCredentials(this.withCredentials);
		loader.load(url, (buffer) => onLoad(VmdObject.ParseFromBuffer(buffer)), onProgress, onError);
	}
	async loadAsync(url, onProgress) {
		return super.loadAsync(url, onProgress);
	}
};

//#endregion
//#region src/physics/grant-solver.ts
/**
* Solver for Grant (Fuyo in Japanese. I just google translated because
* Fuyo may be MMD specific term and may not be common word in 3D CG terms.)
* Grant propagates a bone's transform to other bones transforms even if
* they are not children.
*/
var GrantSolver = class {
	constructor(mesh, grants = []) {
		this.q = new Quaternion();
		this.mesh = mesh;
		this.grants = grants;
	}
	addGrantRotation(bone, q, ratio) {
		this.q.set(0, 0, 0, 1);
		this.q.slerp(q, ratio);
		bone.quaternion.multiply(this.q);
		return this;
	}
	update() {
		const grants = this.grants;
		for (let i = 0, il = grants.length; i < il; i++) this.updateOne(grants[i]);
		return this;
	}
	updateOne(grant) {
		const bones = this.mesh.skeleton.bones;
		const bone = bones[grant.index];
		const parentBone = bones[grant.parentIndex];
		if (grant.isLocal) {} else if (grant.affectRotation) this.addGrantRotation(bone, parentBone.quaternion, grant.ratio);
		return this;
	}
};

//#endregion
//#region src/physics/process-bones.ts
const processBones = () => {
	let backupBones;
	const restoreBones = (mesh) => {
		if (backupBones === void 0) return;
		mesh.skeleton.bones.forEach((bone, i) => {
			bone.position.fromArray(backupBones, i * 7);
			bone.quaternion.fromArray(backupBones, i * 7 + 3);
		});
	};
	const saveBones = (mesh) => {
		const bones = mesh.skeleton.bones;
		if (backupBones === void 0) backupBones = new Float32Array(bones.length * 7);
		mesh.skeleton.bones.forEach((bone, i) => {
			bone.position.toArray(backupBones, i * 7);
			bone.quaternion.toArray(backupBones, i * 7 + 3);
		});
	};
	return {
		restoreBones,
		saveBones
	};
};

//#endregion
//#region src/utils/build-animation.ts
var AnimationBuilder = class {
	/**
	* @param vmd - parsed VMD data
	* @param mesh - tracks will be fitting to mesh
	*/
	build(vmd, mesh) {
		const tracks = this.buildSkeletalAnimation(vmd, mesh).tracks;
		const tracks2 = this.buildMorphAnimation(vmd, mesh).tracks;
		for (let i = 0, il = tracks2.length; i < il; i++) tracks.push(tracks2[i]);
		return new AnimationClip("", -1, tracks);
	}
	/** @param vmd - parsed VMD data */
	buildCameraAnimation(vmd) {
		const pushVector3 = (array, vec) => {
			array.push(vec.x);
			array.push(vec.y);
			array.push(vec.z);
		};
		const pushQuaternion = (array, q) => {
			array.push(q.x);
			array.push(q.y);
			array.push(q.z);
			array.push(q.w);
		};
		const pushInterpolation = (array, interpolation, index) => {
			array.push(interpolation[index * 4 + 0] / 127);
			array.push(interpolation[index * 4 + 1] / 127);
			array.push(interpolation[index * 4 + 2] / 127);
			array.push(interpolation[index * 4 + 3] / 127);
		};
		const cameras = [];
		for (let i = 0; i < vmd.cameraKeyFrames.length; i++) cameras.push(vmd.cameraKeyFrames.get(i));
		cameras.sort((a, b) => a.frameNumber - b.frameNumber);
		const times = [];
		const centers = [];
		const quaternions = [];
		const positions = [];
		const fovs = [];
		const cInterpolations = [];
		const qInterpolations = [];
		const pInterpolations = [];
		const fInterpolations = [];
		const quaternion = new Quaternion();
		const euler = new Euler();
		const position = new Vector3();
		const center = new Vector3();
		for (let i = 0, il = cameras.length; i < il; i++) {
			const motion = cameras[i];
			const time = motion.frameNumber / 30;
			const pos = motion.position;
			const rot = motion.rotation;
			const distance = motion.distance;
			const fov = motion.fov;
			const interpolation = Array.from(motion.interpolation);
			times.push(time);
			position.set(0, 0, -distance);
			center.set(pos[0], pos[1], pos[2]);
			euler.set(-rot[0], -rot[1], -rot[2]);
			quaternion.setFromEuler(euler);
			position.add(center);
			position.applyQuaternion(quaternion);
			pushVector3(centers, center);
			pushQuaternion(quaternions, quaternion);
			pushVector3(positions, position);
			fovs.push(fov);
			for (let j = 0; j < 3; j++) pushInterpolation(cInterpolations, interpolation, j);
			pushInterpolation(qInterpolations, interpolation, 3);
			for (let j = 0; j < 3; j++) pushInterpolation(pInterpolations, interpolation, 4);
			pushInterpolation(fInterpolations, interpolation, 5);
		}
		const tracks = [];
		tracks.push(this._createTrack("target.position", VectorKeyframeTrack, times, centers, cInterpolations));
		tracks.push(this._createTrack(".quaternion", QuaternionKeyframeTrack, times, quaternions, qInterpolations));
		tracks.push(this._createTrack(".position", VectorKeyframeTrack, times, positions, pInterpolations));
		tracks.push(this._createTrack(".fov", NumberKeyframeTrack, times, fovs, fInterpolations));
		return new AnimationClip("", -1, tracks);
	}
	_createTrack(node, TypedKeyframeTrack, times, values, interpolations) {
		if (times.length > 2) {
			times = times.slice();
			values = values.slice();
			interpolations = interpolations.slice();
			const stride = values.length / times.length;
			const interpolateStride = interpolations.length / times.length;
			let index = 1;
			for (let aheadIndex = 2, endIndex = times.length; aheadIndex < endIndex; aheadIndex++) {
				for (let i = 0; i < stride; i++) if (values[index * stride + i] !== values[(index - 1) * stride + i] || values[index * stride + i] !== values[aheadIndex * stride + i]) {
					index++;
					break;
				}
				if (aheadIndex > index) {
					times[index] = times[aheadIndex];
					for (let i = 0; i < stride; i++) values[index * stride + i] = values[aheadIndex * stride + i];
					for (let i = 0; i < interpolateStride; i++) interpolations[index * interpolateStride + i] = interpolations[aheadIndex * interpolateStride + i];
				}
			}
			times.length = index + 1;
			values.length = (index + 1) * stride;
			interpolations.length = (index + 1) * interpolateStride;
		}
		const track = new TypedKeyframeTrack(node, times, values);
		track.createInterpolant = function InterpolantFactoryMethodCubicBezier(result) {
			return new CubicBezierInterpolation(this.times, this.values, this.getValueSize(), result, new Float32Array(interpolations));
		};
		return track;
	}
	/**
	* @param vmd - parsed VMD data
	* @param mesh - tracks will be fitting to mesh
	*/
	buildMorphAnimation(vmd, mesh) {
		const tracks = [];
		const morphs = {};
		const morphTargetDictionary = mesh.morphTargetDictionary;
		for (let i = 0; i < vmd.morphKeyFrames.length; i++) {
			const morph = vmd.morphKeyFrames.get(i);
			const morphName = morph.morphName;
			if (morphTargetDictionary[morphName] == null) continue;
			morphs[morphName] = morphs[morphName] ?? [];
			morphs[morphName].push(morph);
		}
		for (const [key, array] of Object.entries(morphs)) {
			array.sort((a, b) => a.frameNumber - b.frameNumber);
			const times = [];
			const values = [];
			for (let i = 0, il = array.length; i < il; i++) {
				times.push(array[i].frameNumber / 30);
				values.push(array[i].weight);
			}
			tracks.push(new NumberKeyframeTrack(`.morphTargetInfluences[${morphTargetDictionary[key]}]`, times, values));
		}
		return new AnimationClip("", -1, tracks);
	}
	/**
	* @param vmd - parsed VMD data
	* @param mesh - tracks will be fitting to mesh
	*/
	buildSkeletalAnimation(vmd, mesh) {
		const pushInterpolation = (array, interpolation, index) => {
			array.push(interpolation[index + 0] / 127);
			array.push(interpolation[index + 8] / 127);
			array.push(interpolation[index + 4] / 127);
			array.push(interpolation[index + 12] / 127);
		};
		const tracks = [];
		const motions = {};
		const bones = mesh.skeleton.bones;
		const boneNameDictionary = {};
		for (let i = 0, il = bones.length; i < il; i++) boneNameDictionary[bones[i].name] = true;
		for (let i = 0; i < vmd.boneKeyFrames.length; i++) {
			const motion = vmd.boneKeyFrames.get(i);
			const boneName = motion.boneName;
			if (boneNameDictionary[boneName] == null) continue;
			motions[boneName] = motions[boneName] ?? [];
			motions[boneName].push(motion);
		}
		for (const [key, array] of Object.entries(motions)) {
			array.sort((a, b) => a.frameNumber - b.frameNumber);
			const times = [];
			const positions = [];
			const rotations = [];
			const pInterpolations = [];
			const rInterpolations = [];
			const basePosition = mesh.skeleton.getBoneByName(key).position.toArray();
			for (let i = 0, il = array.length; i < il; i++) {
				const time = array[i].frameNumber / 30;
				const position = array[i].position;
				const rotation = array[i].rotation;
				const interpolation = Array.from(array[i].interpolation);
				times.push(time);
				positions.push(basePosition[0] + position[0]);
				positions.push(basePosition[1] + position[1]);
				positions.push(basePosition[2] - position[2]);
				rotations.push(-rotation[0]);
				rotations.push(-rotation[1]);
				rotations.push(rotation[2]);
				rotations.push(rotation[3]);
				for (let j = 0; j < 3; j++) pushInterpolation(pInterpolations, interpolation, j);
				pushInterpolation(rInterpolations, interpolation, 3);
			}
			const targetName = `.bones[${key}]`;
			tracks.push(this._createTrack(`${targetName}.position`, VectorKeyframeTrack, times, positions, pInterpolations));
			tracks.push(this._createTrack(`${targetName}.quaternion`, QuaternionKeyframeTrack, times, rotations, rInterpolations));
		}
		return new AnimationClip("", -1, tracks);
	}
};
var CubicBezierInterpolation = class extends Interpolant {
	constructor(parameterPositions, sampleValues, sampleSize, resultBuffer, params) {
		super(parameterPositions, sampleValues, sampleSize, resultBuffer);
		this.interpolationParams = params;
	}
	_calculate(x1, x2, y1, y2, x) {
		let c = .5;
		let t = c;
		let s = 1 - t;
		const loop = 15;
		const eps = 1e-5;
		const math = Math;
		let sst3, stt3, ttt;
		for (let i = 0; i < loop; i++) {
			sst3 = 3 * s * s * t;
			stt3 = 3 * s * t * t;
			ttt = t * t * t;
			const ft = sst3 * x1 + stt3 * x2 + ttt - x;
			if (math.abs(ft) < eps) break;
			c /= 2;
			t += ft < 0 ? c : -c;
			s = 1 - t;
		}
		return sst3 * y1 + stt3 * y2 + ttt;
	}
	interpolate_(i1, t0, t, t1) {
		const result = this.resultBuffer;
		const values = this.sampleValues;
		const stride = this.valueSize;
		const params = this.interpolationParams;
		const offset1 = i1 * stride;
		const offset0 = offset1 - stride;
		const weight1 = t1 - t0 < 1 / 30 * 1.5 ? 0 : (t - t0) / (t1 - t0);
		if (stride === 4) {
			const x1 = params[i1 * 4 + 0];
			const x2 = params[i1 * 4 + 1];
			const y1 = params[i1 * 4 + 2];
			const y2 = params[i1 * 4 + 3];
			const ratio = this._calculate(x1, x2, y1, y2, weight1);
			Quaternion.slerpFlat(result, 0, values, offset0, values, offset1, ratio);
		} else if (stride === 3) for (let i = 0; i < stride; ++i) {
			const x1 = params[i1 * 12 + i * 4 + 0];
			const x2 = params[i1 * 12 + i * 4 + 1];
			const y1 = params[i1 * 12 + i * 4 + 2];
			const y2 = params[i1 * 12 + i * 4 + 3];
			const ratio = this._calculate(x1, x2, y1, y2, weight1);
			result[i] = values[offset0 + i] * (1 - ratio) + values[offset1 + i] * ratio;
		}
		else {
			const x1 = params[i1 * 4 + 0];
			const x2 = params[i1 * 4 + 1];
			const y1 = params[i1 * 4 + 2];
			const y2 = params[i1 * 4 + 3];
			const ratio = this._calculate(x1, x2, y1, y2, weight1);
			result[0] = values[offset0] * (1 - ratio) + values[offset1] * ratio;
		}
		return result;
	}
};
/**
* @param vmd - parsed VMD data
* @param mesh - tracks will be fitting to mesh
*/
const buildAnimation = (vmd, mesh) => new AnimationBuilder().build(vmd, mesh);
/** @param vmd - parsed VMD data */
const buildCameraAnimation = (vmd) => new AnimationBuilder().buildCameraAnimation(vmd);

//#endregion
export { GrantSolver, MMD, MMDLoader, MMDMeshLoader, PmxObject, VMDLoader, VmdObject, buildAnimation, buildBones, buildCameraAnimation, buildGeometry, buildGrants, buildIK, buildMaterial, buildMesh, processBones };