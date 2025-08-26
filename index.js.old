//CS55-13-Week 01
//use httpp package (shared code) from node.js
let myhttp = require("http");

// use http package createServer()
// that runs a web server
let myserver = myhttp.createServer(
    // createServer() uses our function
    function( myrequest, myresponse ) {
        console.log( myrequest.url );
        
        let mytext;
        if ( myrequest.url === '/hey' ) {
            mytext = "well hello there..";
        } else { 
            mytext = "I don't know you!";
        }

        // writeHead() creates an http response header, including the status code (200 OK),
        // writeHead() takes 1 args: http status code, MIME type
        myresponse.writeHead ( 200, { "Content-Type": "text/plain" } );
        
        //end() returns some data and closes the response (sends it)
        myresponse.end( mytext + "\n" );
    }
);

// ask http to start listening on a tcp port for incoming http requests
// 
myserver.listen( 8080 , "127.0.0.1" );