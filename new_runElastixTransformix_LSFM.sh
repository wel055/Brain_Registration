#!/bin/sh
export DATASET_HOME=`pwd`
DATASET_HOME=`echo $DATASET_HOME/`
FOLDER=${PWD##*/} 
export FIJI_EXE=/usr/local/Fiji.app/ImageJ-linux64
export PREPROCESSING_SCRIPT=/data/rmunozca/UClear-OstenRef_RegistrationFiles/preProcessing.py
export WARPINGIMAGE=*downsampled.tif
export INPUTFILE=`expr "$DATASET_HOME""$WARPINGIMAGE"`
echo $INPUTFILE
echo $DATASET_HOME

#$FIJI_EXE $PREPROCESSING_SCRIPT $INPUTFILE

DATASET_NAME=${PWD##*/}
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export MOVING_IMAGE="$SCRIPT_DIR/CCFv3_25um.tif"
if [ -z "$1" ]; then
	echo "Error: No input image provided. Usage: $0 <fixed_image>"
	exit 1
fi
export FIXED_IMAGE="$1" #`expr "$DATASET_HOME"/coronal_Ex_445_Ch0_stitched.tif`

export AFFINEPARFILE="$SCRIPT_DIR/Par0000affine_rmc.txt"
export BSPLINEPARFILE="$SCRIPT_DIR/Par0000bspline_rmc.txt"

ELASTIX_DIR=/data/software/elastix-5.2.0-linux/
export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:$ELASTIX_DIR/lib/
export ELASTIX=$ELASTIX_DIR/bin/elastix
export ELASTIX_OUTPUT_DIR=elastixOutput
mkdir $ELASTIX_OUTPUT_DIR

#$ELASTIX -threads 24 -f $FIXED_IMAGE -m $MOVING_IMAGE -p $AFFINEPARFILE -p $BSPLINEPARFILE -out $ELASTIX_OUTPUT_DIR
#$ELASTIX -threads 24 -f $FIXED_IMAGE -m $MOVING_IMAGE -p $AFFINEPARFILE -p $BSPLINEPARFILE -out $ELASTIX_OUTPUT_DIR -t0 t0.txt
#$ELASTIX -threads 24 -f $MOVING_IMAGE -m $MOVING_IMAGE -p $AFFINEPARFILE -p $BSPLINEPARFILE -out $ELASTIX_OUTPUT_DIR -t0 t0.txt
#t0_mip3_25um_scaling.txt
#exit 

cd ./elastixOutput
CONVERT3D_BIN=/data/software/c3d-1.4.4-Linux-gcc64/bin/c3d
#$CONVERT3D_BIN result.1.mhd -o result.1.tif
cd -


#export ANNOTATIONFILE=/mnt/brainmapstore/rmunozca/YoungGyumMIT/Ex_1_Em_1_destriped_stitched_illuc/warping/Ex_1_Em_1_destriped_stitched_illuc_ch1_0.2Z_p05.tif
export ANNOTATIONFILE='/home/rmunozca/Documents/scripts/OR_ARA_CCF_25um.tif'
export ANNOTATIONFILE='/data/shang/data/regpipe/from_Xiaoman/CCFv3_Atlas.tif'
#export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/Users/rmunozca/Documents/elastix_linux64_v4/lib/
export TRANSFORMIX=$ELASTIX_DIR/bin/transformix

cd elastixOutput*

# Elastix automatically sets the following parameters in the output transform files, and we need to modify them.
# Simply deleting ResultImagePixelType wouldn't work and still results in 16bit images, and setting it to "long" resulting
# it generating "long long" nrrd files or error on tiffs, and "uint" resulting in blank images in nrrd or segfaults in
# tiff. Float becomes the only viable option. Compressed NRRDs are a lot smaller than the (compressed) TIFFs.
#--
# // Resampler specific
# (CompressResultImage "false")
# (DefaultPixelValue 0)
# (Resampler "DefaultResampler")
# (ResultImageFormat "mhd")
# (ResultImagePixelType "short")
#--

cp TransformParameters.0.txt TransformParameters_labels.0.txt
cp TransformParameters.1.txt TransformParameters_labels.1.txt
sed -i 's/TransformParameters.0.txt/TransformParameters_labels.0.txt/g' TransformParameters_labels.1.txt

sed -i 's/FinalBSplineInterpolationOrder 3/FinalBSplineInterpolationOrder 0/g' TransformParameters_labels.1.txt
#sed -i 's/(ResultImageFormat "mhd")/(ResultImageFormat "nrrd")/g' TransformParameters_labels.0.txt
sed -i 's/(ResultImageFormat "mhd")/(ResultImageFormat "nrrd")/g' TransformParameters_labels.1.txt
#sed -i 's/(ResultImageFormat "mhd")/(ResultImageFormat "tiff")/g' TransformParameters_labels.1.txt
# sed -i 's/(Size 456 528 800)/(Size 456 528 800)/g' TransformParameters_labels.0.txt
# sed -i 's/(Size 456 528 800)/(Size 456 528 800)/g' TransformParameters_labels.1.txt
sed -i 's/(ResultImagePixelType "short")/(ResultImagePixelType "float")/g' TransformParameters_labels.0.txt
sed -i 's/(ResultImagePixelType "short")/(ResultImagePixelType "float")/g' TransformParameters_labels.1.txt

## These don't work:
# sed -i 's/(ResultImagePixelType "short")//g' TransformParameters_labels.0.txt
# sed -i 's/(ResultImagePixelType "short")//g' TransformParameters_labels.1.txt
# sed -i 's/(ResultImagePixelType "short")/(ResultImagePixelType "long")/g' TransformParameters_labels.0.txt
# sed -i 's/(ResultImagePixelType "short")/(ResultImagePixelType "long")/g' TransformParameters_labels.1.txt
# sed -i 's/(MovingInternalImagePixelType "float")//g' TransformParameters_labels.0.txt
# sed -i 's/(MovingInternalImagePixelType "float")//g' TransformParameters_labels.1.txt
# sed -i 's/(MovingInternalImagePixelType "float")/(MovingInternalImagePixelType "long")/g' TransformParameters_labels.0.txt
# sed -i 's/(MovingInternalImagePixelType "float")/(MovingInternalImagePixelType "long")/g' TransformParameters_labels.1.txt
# sed -i 's/(FixedInternalImagePixelType "float")/(FixedInternalImagePixelType "long")/g' TransformParameters_labels.0.txt
# sed -i 's/(FixedInternalImagePixelType "float")/(FixedInternalImagePixelType "long")/g' TransformParameters_labels.1.txt
# sed -i 's/(ResultImagePixelType "short")/(ResultImagePixelType "uint")/g' TransformParameters_labels.0.txt
# sed -i 's/(ResultImagePixelType "short")/(ResultImagePixelType "uint")/g' TransformParameters_labels.1.txt


sed -i 's/(CompressResultImage "false")/(CompressResultImage "true")/g' TransformParameters_labels.1.txt

sed -i 's/(FinalBSplineInterpolationOrder 3)/(FinalBSplineInterpolationOrder 0)/g' TransformParameters_labels.0.txt
sed -i 's/(FinalBSplineInterpolationOrder 3)/(FinalBSplineInterpolationOrder 0)/g' TransformParameters_labels.1.txt

TRANSFORM_DIR=$(basename "$1" tif)_transformix
mkdir "$TRANSFORM_DIR"
cp -p *.log Transform*.txt "$TRANSFORM_DIR"
cd -

mkdir transformixOutput
export TRANSFORMIXOUTPUT=transformixOutput
$TRANSFORMIX -threads 16 -in $MOVING_IMAGE -tp ./elastixOutput/TransformParameters_labels.1.txt -out $TRANSFORMIXOUTPUT
#$TRANSFORMIX -threads 16 -in $MOVING_IMAGE -tp ./elastixOutput/TransformParameters.1.txt -out $TRANSFORMIXOUTPUT

cd ./transformixOutput
#OUTPUT_FILE="${FOLDER}_$(basename "$1" .tif)_registered.tif"
OUTPUT_FILE="$(basename "$1" tif)_registered_CCFv3_template.tif"
$CONVERT3D_BIN result.nrrd -type ushort -compress -o "$OUTPUT_FILE"
#rm result.*
mv result.nrrd "$(basename "$OUTPUT_FILE" .tif).nrrd"
mv result.tiff "$(basename "$OUTPUT_FILE")"
cd -

$TRANSFORMIX -threads 16 -in $ANNOTATIONFILE -tp ./elastixOutput/TransformParameters_labels.1.txt -out $TRANSFORMIXOUTPUT

cd ./transformixOutput
#OUTPUT_FILE="${FOLDER}_$(basename "$1" .tif)_registered.tif"
OUTPUT_FILE="$(basename "$1" tif)_annotation.tif"
echo 1
#"ulong" silently fails, "uint" throws error:
#$CONVERT3D_BIN result.nrrd -type uint -compress -o x"$OUTPUT_FILE"
#  ITK Exception: /data/hippogang/build/buildbot/Nightly/itk/v5.2.1/itk/Modules/IO/TIFF/src/itkTIFFImageIO.cxx:603:
# ITK ERROR: TIFFImageIO(0x646819054b80): TIFF supports unsigned/signed char, unsigned/signed short, and float

echo 2
$CONVERT3D_BIN result.nrrd -compress -o "$OUTPUT_FILE"
echo 3
mv result.nrrd "$(basename "$OUTPUT_FILE" .tif).nrrd"
mv result.tiff "$(basename "$OUTPUT_FILE")"
cd -
