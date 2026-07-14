# Interactive 3D Brain Viewer

The interactive viewer is:

```text
Brain_Registration_Interactive_3D.html
```

It is self-contained and does not download JavaScript or data from the
internet. Drag to rotate, scroll to zoom, shift-drag to pan, and click legend
entries to show or hide the sample and warped CCF surfaces.

## Use From PowerPoint or Google Slides on This Mac

In Terminal, run:

```bash
cd "/Users/wenxili/Desktop/Weil Cornell/Lab/Proj_reg_brain"
./start_brain_3d_viewer.sh
```

Keep that Terminal window open. Slide 8 links to:

```text
http://localhost:8000/Brain_Registration_Interactive_3D.html
```

## Present From Another Computer

Google Slides cannot embed a live WebGL/3D canvas. Upload the HTML file to a
web host that serves static HTML, then replace the slide 8 link with its public
HTTPS URL. Google Drive stores HTML files but does not serve them as an
interactive website.
